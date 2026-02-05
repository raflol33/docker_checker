import docker
import paramiko
import io
import json
import asyncio
from typing import List, Dict, Any
from .database import DockerHost

# Helper to format container data uniformly
from datetime import datetime, timezone
# import dateutil.parser # Removed to avoid dependency issue

# Helper to format container data uniformly
def format_container(host_name: str, c: Dict[str, Any]) -> Dict[str, Any]:
    # Names
    # 'Names' (list) comes from 'docker ps' (remote/cli).
    # 'Name' (str) comes from 'docker inspect' (local/sdk).
    name_val = c.get('Names')
    if name_val and isinstance(name_val, list):
         name = name_val[0].lstrip('/')
    elif 'Name' in c and isinstance(c['Name'], str):
         name = c['Name'].lstrip('/')
    else:
         name = "Unknown"
    
    # ... (Image logic unchanged) ...
    image = "Unknown"
    if 'Config' in c and isinstance(c['Config'], dict) and 'Image' in c['Config']:
        image = c['Config']['Image']
    elif 'Image' in c:
        image = c['Image']
    
    # State & Status
    state = "unknown"
    if isinstance(c.get('State'), dict):
        state = c['State'].get('Status', 'unknown')
    else:
        state = c.get('State', 'unknown')
        
    # Status (Uptime) - Calculate manually if missing
    status_text = c.get('Status') # Try top level
    
    if not status_text and 'State' in c and isinstance(c['State'], dict):
        started_at = c['State'].get('StartedAt')
        # e.g. '2026-01-27T22:00:13.257652099Z'
        if started_at:
            try:
                # Truncate nanoseconds for strptime or use basic slicing
                # Python 3.11 supports isoformat well.
                start_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                now_dt = datetime.now(timezone.utc)
                diff = now_dt - start_dt
                
                days = diff.days
                seconds = diff.seconds
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                
                if state.lower() == 'running':
                    if days > 0:
                        status_text = f"Up {days} days"
                    elif hours > 0:
                        status_text = f"Up {hours} hours"
                    elif minutes > 0:
                        status_text = f"Up {minutes} mins"
                    else:
                        status_text = "Up < 1 min"
                else:
                    status_text = f"Exited" # Simplification for non-running
            except Exception:
                status_text = "Unknown"
    
    if not status_text:
        status_text = "Unknown"
    
    # Ports
    ports_list = []
    # SDK
    if 'NetworkSettings' in c and 'Ports' in c['NetworkSettings'] and isinstance(c['NetworkSettings']['Ports'], dict):
        for port, bindings in c['NetworkSettings']['Ports'].items():
            if bindings:
                for bind in bindings:
                     if bind and 'HostPort' in bind:
                        ports_list.append(f"{bind.get('HostPort')}->{port}")
            else:
                ports_list.append(f"{port}")
    # CLI JSON (remapped or raw)
    elif 'Ports' in c:
        p = c['Ports']
        if p:
            ports_list.append(str(p))
            
    ports_str = ", ".join(ports_list)
    
    # Created
    created = c.get('Created', c.get('CreatedAt', 'Unknown'))
    # Clean up CLI format like "2023-01-01 10:00:00 +0000 UTC" -> "2023-01-01 10:00"
    if created and len(created) > 19: 
        created = created.replace('T', ' ')[:19]

    # Labels & Compose Path
    labels = {}
    if 'Config' in c and 'Labels' in c['Config']:
        labels = c['Config']['Labels'] or {}
    elif 'Labels' in c:
        # CLI JSON: Labels can be "key=value,key2=val2" string OR dict depending on version/format
        # "docker ps --format json" usually returns a comma-separated string for .Labels if not using {{json .}} properly
        # But we use {{json .}} which returns object/map for Labels in newer Docker, 
        # or string in older. Let's handle dict primarily as we use {{json .}}.
        if isinstance(c['Labels'], dict):
            labels = c['Labels']
        elif isinstance(c['Labels'], str):
            # Try to parse properties string "key=value,key2=val"
            # Fallback: Just look for the specific substring we need if parsing is too complex
            raw_labels = c['Labels']
            if 'com.docker.compose.project.working_dir=' in raw_labels:
                # Extract value manually
                try:
                    start = raw_labels.find('com.docker.compose.project.working_dir=') + len('com.docker.compose.project.working_dir=')
                    # Find end of value (comma or end of string)
                    end = raw_labels.find(',', start)
                    if end == -1:
                        end = len(raw_labels)
                    labels['com.docker.compose.project.working_dir'] = raw_labels[start:end].strip()
                except:
                    pass
            
            # Still try to parse others roughly
            parts = raw_labels.split(',')
            for part in parts:
                if '=' in part:
                    k, v = part.split('=', 1)
                    labels[k.strip()] = v.strip()
        
    compose_path = labels.get('com.docker.compose.project.working_dir', '')

    return {
        "id": c.get("Id", c.get("ID", ""))[:12],
        "name": name,
        "image": image,
        # "image_tag": ... (simplifying, full image name is better)
        "state": state,
        "status": status_text,
        "ports": ports_str,
        "created": created,
        "host": host_name,
        "compose_path": compose_path
    }


class DockerService:
    @staticmethod
    def get_local_client():
        return docker.from_env()

    @staticmethod
    async def get_ssh_client(host: DockerHost) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": host.ip,
            "port": host.port or 22,
            "username": host.ssh_user
        }
        
        # Priority: 1. SSH Key from DB (encrypted), 2. Password, 3. Key file path
        if host.ssh_key and host.ssh_key.private_key:
            import io
            from cryptography.fernet import Fernet
            import base64
            import hashlib
            
            private_key_data = host.ssh_key.private_key
            
            # Try to decrypt if key password is provided
            if host.ssh_key_password:
                try:
                    key = hashlib.sha256(host.ssh_key_password.encode()).digest()
                    fernet_key = base64.urlsafe_b64encode(key)
                    fernet = Fernet(fernet_key)
                    private_key_data = fernet.decrypt(private_key_data.encode()).decode()
                except Exception:
                    pass  # Key might not be encrypted
            
            key_file = io.StringIO(private_key_data)
            try:
                pkey = paramiko.RSAKey.from_private_key(key_file)
            except:
                key_file.seek(0)
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(key_file)
                except:
                    key_file.seek(0)
                    pkey = paramiko.ECDSAKey.from_private_key(key_file)
            connect_kwargs["pkey"] = pkey
        elif host.ssh_password:
            connect_kwargs["password"] = host.ssh_password
        elif host.ssh_key_path:
            connect_kwargs["key_filename"] = host.ssh_key_path
            
        def connect():
            client.connect(**connect_kwargs)
        
        await asyncio.to_thread(connect)
        return client


    @staticmethod
    async def get_container_stats(host: DockerHost, loop: asyncio.AbstractEventLoop) -> Dict[str, Dict[str, Any]]:
        """
        Get CPU/Memory stats for all running containers.
        Returns dict keyed by container ID with cpu_percent, mem_percent, mem_usage, mem_limit.
        """
        stats = {}
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            containers = await loop.run_in_executor(None, lambda: client.containers.list(filters={"status": "running"}))
            
            for container in containers:
                try:
                    # Get one-shot stats
                    raw_stats = await loop.run_in_executor(None, lambda c=container: c.stats(stream=False))
                    
                    # Calculate CPU percentage
                    cpu_delta = raw_stats['cpu_stats']['cpu_usage']['total_usage'] - \
                                raw_stats['precpu_stats']['cpu_usage']['total_usage']
                    system_delta = raw_stats['cpu_stats']['system_cpu_usage'] - \
                                   raw_stats['precpu_stats']['system_cpu_usage']
                    cpu_count = raw_stats['cpu_stats'].get('online_cpus', 1)
                    
                    cpu_percent = 0.0
                    if system_delta > 0:
                        cpu_percent = (cpu_delta / system_delta) * cpu_count * 100
                    
                    # Memory stats
                    mem_usage = raw_stats['memory_stats'].get('usage', 0)
                    mem_limit = raw_stats['memory_stats'].get('limit', 1)
                    mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0
                    
                    stats[container.id[:12]] = {
                        "cpu_percent": round(cpu_percent, 1),
                        "mem_percent": round(mem_percent, 1),
                        "mem_usage": mem_usage,
                        "mem_limit": mem_limit
                    }
                except Exception:
                    pass
        else:
            # SSH: use docker stats --no-stream --format
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = 'docker stats --no-stream --format "{{.ID}}|{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}"'
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                output_str = output.decode()
                
                for line in output_str.strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 4:
                            container_id = parts[0][:12]
                            cpu_str = parts[1].replace('%', '').strip()
                            mem_str = parts[2].replace('%', '').strip()
                            mem_usage_str = parts[3]  # e.g., "50MiB / 1GiB"
                            
                            try:
                                cpu_percent = float(cpu_str) if cpu_str else 0
                                mem_percent = float(mem_str) if mem_str else 0
                            except ValueError:
                                cpu_percent = 0
                                mem_percent = 0
                            
                            stats[container_id] = {
                                "cpu_percent": round(cpu_percent, 1),
                                "mem_percent": round(mem_percent, 1),
                                "mem_usage_str": mem_usage_str
                            }
            finally:
                ssh.close()
        
        return stats

    @staticmethod
    async def list_containers(host: DockerHost, loop: asyncio.AbstractEventLoop) -> List[Dict[str, Any]]:

        if host.type == 'local':
            client = DockerService.get_local_client()
            # SDK is blocking
            containers = await loop.run_in_executor(None, client.containers.list, True) # all=True
            return [format_container(host.name, c.attrs) for c in containers]
        else:
            # Remote SSH
            ssh = await DockerService.get_ssh_client(host)
            try:
                # Use --format json
                command = "docker ps -a --format '{{json .}}'"
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                output_str = output.decode()
                
                containers = []
                for line in output_str.strip().split('\n'):
                    if line:
                        try:
                            data = json.loads(line)
                            # Remap keys to match SDK attrs mostly
                            # CLI JSON keys: Command, CreatedAt, ID, Image, Labels, LocalVolumes, Mounts, Names, Networks, Ports, RunningFor, Size, State, Status
                            
                            # Parse labels string "key=value,key2=val" into dict
                            labels_str = data.get("Labels", "")
                            labels = {}
                            if labels_str:
                                for l in labels_str.split(','):
                                    if '=' in l:
                                        k, v = l.split('=', 1)
                                        labels[k] = v
                                    else:
                                        labels[l] = ""

                            remapped = {
                                "Id": data.get("ID"),
                                "Names": [data.get("Names")],
                                "Image": data.get("Image"),
                                "State": data.get("State"), # e.g. "running"
                                "Status": data.get("Status"), # e.g. "Up 2 hours"
                                "Ports": data.get("Ports"),
                                "Created": data.get("CreatedAt"),
                                "Labels": labels 
                            }
                            containers.append(format_container(host.name, remapped))
                        except json.JSONDecodeError:
                            continue
                return containers
            finally:
                ssh.close()
                
    @staticmethod
    async def list_images(host: DockerHost, loop: asyncio.AbstractEventLoop) -> List[Dict[str, Any]]:
        if host.type == 'local':
            client = DockerService.get_local_client()
            images = await loop.run_in_executor(None, client.images.list)
            # Format images
            res = []
            for i in images:
                tags = i.tags if i.tags else [i.short_id]
                for tag in tags:
                    res.append({
                        "id": i.short_id,
                        "tag": tag,
                        "created": i.attrs.get('Created', '')[:19].replace('T', ' '),
                        "size": f"{i.attrs.get('Size', 0) // (1024*1024)} MB"
                    })
            return res
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = "docker images --format '{{json .}}'"
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                output_str = output.decode()
                
                images = []
                for line in output_str.strip().split('\n'):
                    if line:
                        try:
                            data = json.loads(line)
                            # CLI keys: Repository, Tag, ID, CreatedSince, CreatedAt, Size
                            repo = data.get("Repository", "<none>")
                            tag = data.get("Tag", "<none>")
                            full_tag = f"{repo}:{tag}"
                            
                            images.append({
                                "id": data.get("ID"),
                                "tag": full_tag,
                                "created": data.get("CreatedAt", "")[:19],
                                "size": data.get("Size")
                            })
                        except json.JSONDecodeError:
                            continue
                return images
            finally:
                ssh.close()


    @staticmethod
    async def restart_container(host: DockerHost, container_id: str, loop: asyncio.AbstractEventLoop):
        if host.type == 'local':
            client = DockerService.get_local_client()
            container = await loop.run_in_executor(None, client.containers.get, container_id)
            await loop.run_in_executor(None, container.restart)
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = f"docker restart {container_id}"
                await loop.run_in_executor(None, ssh.exec_command, command)
            finally:
                ssh.close()

    @staticmethod
    async def stop_container(host: DockerHost, container_id: str, loop: asyncio.AbstractEventLoop):
        if host.type == 'local':
            client = DockerService.get_local_client()
            container = await loop.run_in_executor(None, client.containers.get, container_id)
            await loop.run_in_executor(None, container.stop)
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = f"docker stop {container_id}"
                await loop.run_in_executor(None, ssh.exec_command, command)
            finally:
                ssh.close()

    @staticmethod
    async def start_container(host: DockerHost, container_id: str, loop: asyncio.AbstractEventLoop):
        if host.type == 'local':
            client = DockerService.get_local_client()
            container = await loop.run_in_executor(None, client.containers.get, container_id)
            await loop.run_in_executor(None, container.start)
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = f"docker start {container_id}"
                await loop.run_in_executor(None, ssh.exec_command, command)
            finally:
                ssh.close()

    @staticmethod
    async def get_logs(host: DockerHost, container_id: str, tail: str, since: str, until: str, search: str, loop: asyncio.AbstractEventLoop) -> str:
        # tail can be int or "all"
        # since/until can be relative string like "5m" or timestamp
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            container = await loop.run_in_executor(None, client.containers.get, container_id)
            
            kwargs = {}
            if tail != 'all':
                try:
                    kwargs['tail'] = int(tail)
                except ValueError:
                    kwargs['tail'] = 'all'
            else:
                kwargs['tail'] = 'all'
                
            if since:
                kwargs['since'] = since
            if until:
                kwargs['until'] = until
                
            try:
                logs_bytes = await loop.run_in_executor(None, lambda: container.logs(**kwargs))
                logs = logs_bytes.decode('utf-8', errors='replace')
                
                if search:
                    return "\n".join([line for line in logs.split('\n') if search.lower() in line.lower()])
                return logs
            except Exception as e:
                return f"Error fetching logs: {str(e)}"
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                # --tail takes "all" or number
                cmd_parts = ["docker", "logs"]
                if tail != 'all':
                    cmd_parts.append(f"--tail {tail}")
                else:
                    cmd_parts.append("--tail all")
                    
                if since:
                    cmd_parts.append(f"--since '{since}'")
                if until:
                    cmd_parts.append(f"--until '{until}'")
                    
                cmd_parts.append(container_id)
                
                command = " ".join(cmd_parts)
                
                # Add grep if search provided (simple grep)
                if search:
                    # Escape search term roughly to prevent injection issues, though this is admin tool
                    clean_search = search.replace("'", "")
                    command += f" | grep -i '{clean_search}'"
                
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                error = await loop.run_in_executor(None, stderr.read)
                return output.decode('utf-8', errors='replace') + error.decode('utf-8', errors='replace')
            finally:
                ssh.close()

    @staticmethod
    async def run_compose(host: DockerHost, path: str, action: str, loop: asyncio.AbstractEventLoop):
        # action: "up", "down", "restart"
        if action == "restart":
            cmd_action = "down && docker-compose up -d"
        else:
            cmd_action = "up -d" if action == "up" else "down"
            
        # path is the directory containing docker-compose.yml
        
        full_command = f"cd {path} && docker-compose {cmd_action}"
        
        if host.type == 'local':
            # Run locally
            proc = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise Exception(f"Compose failed: {stderr.decode()}")
            return stdout.decode()
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, full_command)
                exit_status = await loop.run_in_executor(None, stdout.channel.recv_exit_status)
                out = await loop.run_in_executor(None, stdout.read)
                err = await loop.run_in_executor(None, stderr.read)
                if exit_status != 0:
                    raise Exception(f"Remote Compose failed: {err.decode()}")
                return out.decode()
                if exit_status != 0:
                    raise Exception(f"Remote Compose failed: {err.decode()}")
                return out.decode()
            finally:
                ssh.close()

    @staticmethod
    async def delete_image(host: DockerHost, image_id: str, loop: asyncio.AbstractEventLoop):
        if host.type == 'local':
            client = DockerService.get_local_client()
            # force=True might be needed if containers stopped but using it? Let's safeguard.
            # Using force=False by default to avoid accidents, user can delete stopped containers first.
            await loop.run_in_executor(None, client.images.remove, image_id)
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = f"docker rmi {image_id}"
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                exit_status = await loop.run_in_executor(None, stdout.channel.recv_exit_status)
                error = await loop.run_in_executor(None, stderr.read)
                if exit_status != 0:
                     raise Exception(f"Failed to remove image: {error.decode()}")
            finally:
                ssh.close()

    @staticmethod
    async def stream_logs(host: DockerHost, container_id: str, tail: str, loop: asyncio.AbstractEventLoop):
        # Generator that yields log lines for WebSocket
        if host.type == 'local':
            client = DockerService.get_local_client()
            
            # Use SDK to stream logs to avoid needing the docker binary
            # The SDK generator is blocking, so we consume it in a thread.
            import queue
            q = queue.Queue()
            
            def local_reader_thread():
                try:
                    # stream=True returns a blocking generator
                    # tail must be int or 'all'
                    t = tail
                    if t != 'all':
                        try:
                            t = int(tail)
                        except:
                            t = 'all'

                    container = client.containers.get(container_id)
                    # logs(stream=True) returns bytes
                    for line in container.logs(stream=True, tail=t, follow=True):
                        q.put(line)
                    q.put(None)
                except Exception as e:
                    q.put(f"Error reading local logs: {e}".encode())
                    q.put(None)

            # Start reader thread as daemon (fire and forget)
            import threading
            t_thread = threading.Thread(target=local_reader_thread, daemon=True)
            t_thread.start()

            while True:
                # Retrieve from queue asynchronously
                chunk = await loop.run_in_executor(None, q.get)
                if chunk is None:
                    break
                yield chunk.decode('utf-8', errors='replace')

        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                # Combining stdout and stderr
                command = f"docker logs -f --tail {tail} {container_id} 2>&1"
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                
                # Paramiko stream reader workaround
                import queue
                q = queue.Queue()
                
                def reader_thread():
                    while True:
                        try:
                            data = stdout.channel.recv(1024)
                            if not data:
                                break
                            q.put(data)
                        except:
                            break
                    q.put(None) 

                # Use threading for remote reader too (critical fix)
                import threading
                t_thread = threading.Thread(target=reader_thread, daemon=True)
                t_thread.start()

                while True:
                    chunk = await loop.run_in_executor(None, q.get)
                    if chunk is None:
                        break
                    yield chunk.decode('utf-8', errors='replace')
            
            except Exception:
                yield f"Error streaming from {host.name}\n"
            finally:
                ssh.close()

    @staticmethod
    async def exec_interactive(host: DockerHost, container_id: str, loop: asyncio.AbstractEventLoop):
        """
        Returns a tuple: (read_generator, write_function, cleanup_function)
        For interactive terminal execution.
        """
        import queue
        import threading
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            container = await loop.run_in_executor(None, client.containers.get, container_id)
            
            # Create exec instance with tty
            exec_id = await loop.run_in_executor(
                None,
                lambda: client.api.exec_create(
                    container.id,
                    '/bin/sh',
                    stdin=True,
                    tty=True,
                    stderr=True,
                    stdout=True
                )
            )
            
            # Start exec with socket
            socket = await loop.run_in_executor(
                None,
                lambda: client.api.exec_start(exec_id, socket=True, tty=True)
            )
            
            sock = socket._sock
            sock.setblocking(False)
            
            q = queue.Queue()
            running = [True]
            
            def reader_thread():
                import select
                while running[0]:
                    try:
                        ready, _, _ = select.select([sock], [], [], 0.1)
                        if ready:
                            data = sock.recv(4096)
                            if data:
                                q.put(data)
                            else:
                                break
                    except:
                        break
                q.put(None)
            
            t = threading.Thread(target=reader_thread, daemon=True)
            t.start()
            
            async def read_output():
                while True:
                    chunk = await loop.run_in_executor(None, q.get)
                    if chunk is None:
                        break
                    yield chunk.decode('utf-8', errors='replace')
            
            def write_input(data: str):
                try:
                    sock.sendall(data.encode())
                except:
                    pass
            
            def cleanup():
                running[0] = False
                try:
                    sock.close()
                except:
                    pass
            
            return read_output(), write_input, cleanup
            
        else:
            # SSH remote exec
            ssh = await DockerService.get_ssh_client(host)
            transport = ssh.get_transport()
            channel = transport.open_session()
            channel.get_pty(term='xterm', width=120, height=40)
            channel.exec_command(f'docker exec -it {container_id} /bin/sh')
            
            q = queue.Queue()
            running = [True]
            
            def reader_thread():
                while running[0]:
                    if channel.recv_ready():
                        data = channel.recv(4096)
                        if data:
                            q.put(data)
                        else:
                            break
                    else:
                        import time
                        time.sleep(0.05)
                q.put(None)
            
            t = threading.Thread(target=reader_thread, daemon=True)
            t.start()
            
            async def read_output():
                while True:
                    chunk = await loop.run_in_executor(None, q.get)
                    if chunk is None:
                        break
                    yield chunk.decode('utf-8', errors='replace')
            
            def write_input(data: str):
                try:
                    channel.send(data.encode())
                except:
                    pass
            
            def cleanup():
                running[0] = False
                try:
                    channel.close()
                    ssh.close()
                except:
                    pass
            
            return read_output(), write_input, cleanup

    # ===== NETWORKS =====
    
    @staticmethod
    async def list_networks(host: DockerHost, loop: asyncio.AbstractEventLoop) -> List[Dict[str, Any]]:
        """List all networks on a host"""
        networks = []
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            net_list = await loop.run_in_executor(None, client.networks.list)
            for net in net_list:
                networks.append({
                    "id": net.id[:12],
                    "name": net.name,
                    "driver": net.attrs.get('Driver', 'unknown'),
                    "scope": net.attrs.get('Scope', 'unknown'),
                    "containers": len(net.attrs.get('Containers', {}) or {})
                })
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = 'docker network ls --format "{{.ID}}|{{.Name}}|{{.Driver}}|{{.Scope}}"'
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                
                for line in output.decode().strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 4:
                            networks.append({
                                "id": parts[0][:12],
                                "name": parts[1],
                                "driver": parts[2],
                                "scope": parts[3],
                                "containers": 0  # Would need additional command
                            })
            finally:
                ssh.close()
        
        return networks
    
    @staticmethod
    async def prune_networks(host: DockerHost, loop: asyncio.AbstractEventLoop) -> List[str]:
        """Remove unused networks"""
        deleted = []
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            result = await loop.run_in_executor(None, client.networks.prune)
            deleted = result.get('NetworksDeleted', []) or []
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = 'docker network prune -f'
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                # Parse output for deleted networks
                for line in output.decode().split('\n'):
                    if line.strip() and not line.startswith('Deleted') and not line.startswith('Total'):
                        deleted.append(line.strip())
            finally:
                ssh.close()
        
        return deleted

    # ===== VOLUMES =====
    
    @staticmethod
    async def list_volumes(host: DockerHost, loop: asyncio.AbstractEventLoop) -> List[Dict[str, Any]]:
        """List all volumes on a host"""
        volumes = []
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            vol_result = await loop.run_in_executor(None, client.volumes.list)
            for vol in vol_result:
                volumes.append({
                    "name": vol.name,
                    "driver": vol.attrs.get('Driver', 'local'),
                    "mountpoint": vol.attrs.get('Mountpoint', ''),
                    "created": vol.attrs.get('CreatedAt', '')[:19] if vol.attrs.get('CreatedAt') else ''
                })
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = 'docker volume ls --format "{{.Name}}|{{.Driver}}|{{.Mountpoint}}"'
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                
                for line in output.decode().strip().split('\n'):
                    if '|' in line:
                        parts = line.split('|')
                        if len(parts) >= 3:
                            volumes.append({
                                "name": parts[0],
                                "driver": parts[1],
                                "mountpoint": parts[2],
                                "created": ""
                            })
            finally:
                ssh.close()
        
        return volumes
    
    @staticmethod
    async def prune_volumes(host: DockerHost, loop: asyncio.AbstractEventLoop) -> tuple:
        """Remove unused volumes. Returns (deleted_names, space_reclaimed_bytes)"""
        deleted = []
        space = 0
        
        if host.type == 'local':
            client = DockerService.get_local_client()
            result = await loop.run_in_executor(None, client.volumes.prune)
            deleted = result.get('VolumesDeleted', []) or []
            space = result.get('SpaceReclaimed', 0)
        else:
            ssh = await DockerService.get_ssh_client(host)
            try:
                command = 'docker volume prune -f'
                stdin, stdout, stderr = await loop.run_in_executor(None, ssh.exec_command, command)
                output = await loop.run_in_executor(None, stdout.read)
                # Parse output
                for line in output.decode().split('\n'):
                    line = line.strip()
                    if line and not line.startswith('Deleted') and not line.startswith('Total'):
                        deleted.append(line)
            finally:
                ssh.close()
        
        return deleted, space
