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
        
        # In a real app, handle keys files properly. 
        # Here we accept key path or password.
        connect_kwargs = {
            "hostname": host.ip,
            "port": host.port or 22,
            "username": host.ssh_user
        }
        
        if host.ssh_password:
            connect_kwargs["password"] = host.ssh_password
        elif host.ssh_key_path:
            connect_kwargs["key_filename"] = host.ssh_key_path
            
        # This is a blocking call, checking how to make it async friendly?
        # Run in executor.
        def connect():
            client.connect(**connect_kwargs)
        
        await asyncio.to_thread(connect)
        return client

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
                            remapped = {
                                "Id": data.get("ID"),
                                "Names": [data.get("Names")],
                                "Image": data.get("Image"),
                                "State": data.get("State"), # e.g. "running"
                                "Status": data.get("Status"), # e.g. "Up 2 hours"
                                "Ports": data.get("Ports"),
                                "Created": data.get("CreatedAt") 
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
        # action: "up" or "down"
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
