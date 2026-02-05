---
description: Full rebuild of docker-manager container with tar export
---

# Docker Manager Full Rebuild

Execute these commands in order from `/home/raf/Documents/testproject/docker_checker`:

// turbo-all

1. Stop and remove container:
```bash
docker-compose down
```

2. Remove old image:
```bash
docker rmi docker-manager:latest
```

3. Remove old tar file:
```bash
sudo rm -f docker-manager.tar
```

4. Build new image and export to tar:
```bash
docker build -t docker-manager:latest . && docker save -o docker-manager.tar docker-manager:latest
```

5. Load image from tar:
```bash
docker load -i docker-manager.tar
```

6. Start container:
```bash
docker-compose up -d
```
