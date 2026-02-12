"""
MapArr v1.0 - Path Mapping Intelligence Backend
Foundation layer: Docker detection + path analysis + API endpoints

This is the core engine that:
1. Detects Docker containers and their volumes
2. Analyzes path configurations
3. Detects conflicts
4. Provides recommendations
"""

import os
import json
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import docker
from docker.errors import DockerException
import yaml

# ═══════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# FASTAPI APP SETUP
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="MapArr v1.0",
    description="Path mapping intelligence for *arr applications",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════
# DOCKER CONNECTION MANAGER
# ═══════════════════════════════════════════════════════════

class DockerManager:
    """
    Manages Docker connection and container discovery.
    Handles both Unix socket and Windows named pipe.
    """
    
    def __init__(self):
        self.client: Optional[docker.DockerClient] = None
        self.is_connected = False
        self.connection_method = None
        self.error = None
        
        # Try to connect
        self._connect()
    
    def _connect(self):
        """Attempt Docker connection with smart detection"""
        
        # Try 1: Unix socket (Linux/WSL)
        try:
            self.client = docker.DockerClient(
                base_url="unix:///var/run/docker.sock"
            )
            self.client.ping()
            self.is_connected = True
            self.connection_method = "unix_socket"
            logger.info("✅ Docker connected via Unix socket")
            return
        except Exception as e:
            logger.debug(f"Unix socket failed: {e}")
        
        # Try 2: Windows named pipe
        try:
            self.client = docker.DockerClient(
                base_url="npipe:////./pipe/docker_engine"
            )
            self.client.ping()
            self.is_connected = True
            self.connection_method = "windows_pipe"
            logger.info("✅ Docker connected via Windows named pipe")
            return
        except Exception as e:
            logger.debug(f"Windows pipe failed: {e}")
        
        # Try 3: Default (DOCKER_HOST env var)
        try:
            self.client = docker.DockerClient()
            self.client.ping()
            self.is_connected = True
            self.connection_method = "docker_host_env"
            logger.info("✅ Docker connected via DOCKER_HOST")
            return
        except Exception as e:
            logger.debug(f"Docker default failed: {e}")
        
        # All attempts failed
        self.is_connected = False
        self.error = "Could not connect to Docker. Please check docker socket mount."
        logger.warning(f"❌ Docker connection failed: {self.error}")
    
    def get_containers(self) -> List[Dict[str, Any]]:
        """
        Get all running containers and their volume mounts.
        Returns empty list if Docker not connected.
        """
        if not self.is_connected:
            return []
        
        try:
            containers = self.client.containers.list()
            result = []
            
            for container in containers:
                container_info = {
                    "id": container.short_id,
                    "name": container.name,
                    "image": container.image.tags[0] if container.image.tags else "unknown",
                    "status": container.status,
                    "volumes": self._extract_volumes(container),
                    "env_vars": self._extract_env_vars(container),
                }
                result.append(container_info)
            
            logger.info(f"📦 Found {len(result)} containers")
            return result
        
        except Exception as e:
            logger.error(f"Error getting containers: {e}")
            return []
    
    def _extract_volumes(self, container) -> Dict[str, str]:
        """Extract volume mounts from container"""
        volumes = {}
        
        try:
            mounts = container.attrs.get('Mounts', [])
            for mount in mounts:
                source = mount.get('Source', '')
                destination = mount.get('Destination', '')
                
                if source and destination:
                    volumes[destination] = source
        
        except Exception as e:
            logger.debug(f"Error extracting volumes: {e}")
        
        return volumes
    
    def _extract_env_vars(self, container) -> Dict[str, str]:
        """Extract environment variables (filtering for path-related ones)"""
        env_vars = {}
        path_keywords = ['path', 'root', 'mount', 'dir', 'folder']
        
        try:
            config = container.attrs.get('Config', {})
            env = config.get('Env', [])
            
            for var in env:
                if '=' in var:
                    key, value = var.split('=', 1)
                    # Only keep vars that look path-related
                    if any(keyword in key.lower() for keyword in path_keywords):
                        env_vars[key] = value
        
        except Exception as e:
            logger.debug(f"Error extracting env vars: {e}")
        
        return env_vars

# ═══════════════════════════════════════════════════════════
# PATH ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════

class PathAnalyzer:
    """
    Analyzes Docker volume configurations and detects conflicts.
    Identifies platform and provides recommendations.
    """
    
    def __init__(self, containers: List[Dict[str, Any]]):
        self.containers = containers
        self.platform = self._detect_platform()
        self.conflicts = []
        self.recommendations = []
    
    def _detect_platform(self) -> str:
        """Infer platform from container configurations"""
        
        # Collect all paths
        all_paths = []
        for container in self.containers:
            all_paths.extend(container['volumes'].values())
        
        # Heuristics
        if any('\\' in path for path in all_paths):
            return "windows"
        
        if any('unraid' in path.lower() for path in all_paths):
            return "unraid"
        
        if any('synology' in path.lower() or 'nas' in path.lower() for path in all_paths):
            return "synology"
        
        if any('/var/lib/docker' in path for path in all_paths):
            return "linux"
        
        if any('/data' in path or '/media' in path for path in all_paths):
            return "docker"
        
        return "unknown"
    
    def analyze(self) -> Dict[str, Any]:
        """Run full analysis"""
        
        self._detect_conflicts()
        self._generate_recommendations()
        
        return {
            "platform": self.platform,
            "containers": self.containers,
            "conflicts": self.conflicts,
            "recommendations": self.recommendations,
            "summary": self._generate_summary()
        }
    
    def _detect_conflicts(self):
        """Detect path mapping conflicts"""
        
        # Look for duplicate destinations
        destination_map = {}
        for container in self.containers:
            for dest, source in container['volumes'].items():
                if dest not in destination_map:
                    destination_map[dest] = []
                destination_map[dest].append({
                    'container': container['name'],
                    'source': source
                })
        
        # Find conflicts
        for dest, mappings in destination_map.items():
            if len(mappings) > 1:
                # Multiple containers mapping to same destination
                sources = set(m['source'] for m in mappings)
                
                if len(sources) > 1:
                    # Different sources to same destination = CONFLICT
                    self.conflicts.append({
                        "type": "multiple_sources",
                        "destination": dest,
                        "containers": [m['container'] for m in mappings],
                        "sources": list(sources),
                        "severity": "high"
                    })
                else:
                    # Same source, multiple containers = OK (sharing)
                    pass
        
        # Look for inconsistent path patterns
        arr_containers = [c for c in self.containers if any(
            arr in c['name'].lower() for arr in ['sonarr', 'radarr', 'lidarr', 'bazarr']
        )]
        
        if arr_containers:
            self._check_arr_consistency(arr_containers)
    
    def _check_arr_consistency(self, arr_containers: List[Dict]):
        """Check if *arr apps use consistent paths"""
        
        # Collect all paths used by arr apps
        paths_by_container = {}
        for container in arr_containers:
            paths = list(container['volumes'].keys())
            paths_by_container[container['name']] = paths
        
        # Check for mismatches
        if len(arr_containers) > 1:
            all_dests = set()
            for paths in paths_by_container.values():
                all_dests.update(paths)
            
            # If arr apps have significantly different path structures, flag it
            for container_name, paths in paths_by_container.items():
                matching = sum(1 for p in all_dests if p in paths)
                if matching < len(all_dests) * 0.7:
                    self.conflicts.append({
                        "type": "arr_path_mismatch",
                        "container": container_name,
                        "severity": "medium",
                        "note": f"{container_name} doesn't share paths with other arr apps"
                    })
    
    def _generate_recommendations(self):
        """Generate recommendations based on analysis"""
        
        if self.platform == "windows":
            self.recommendations.append({
                "priority": "high",
                "title": "WSL2 Path Format",
                "description": "Convert Windows paths to WSL2 format for consistency",
                "example": "C:\\data → /mnt/c/data"
            })
        
        if any(c['type'] == 'multiple_sources' and c['severity'] == 'high' for c in self.conflicts):
            self.recommendations.append({
                "priority": "high",
                "title": "Unify Path Mappings",
                "description": "Multiple containers mapping to same destination with different sources",
                "action": "Use identical source paths across all containers"
            })
        
        if self.platform == "unknown":
            self.recommendations.append({
                "priority": "medium",
                "title": "Clarify Setup",
                "description": "We couldn't auto-detect your platform",
                "action": "Tell us your setup and we'll provide specific guidance"
            })
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Generate human-readable summary"""
        
        return {
            "platform_detected": self.platform,
            "containers_analyzed": len(self.containers),
            "conflicts_found": len([c for c in self.conflicts if c['severity'] == 'high']),
            "warnings_found": len([c for c in self.conflicts if c['severity'] == 'medium']),
            "status": "healthy" if not self.conflicts else "needs_attention"
        }

# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

docker_manager = DockerManager()

# ═══════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "docker_connected": docker_manager.is_connected
    }

@app.get("/api/docker/status")
async def docker_status():
    """Get Docker connection status"""
    return {
        "connected": docker_manager.is_connected,
        "method": docker_manager.connection_method,
        "error": docker_manager.error
    }

@app.get("/api/containers")
async def list_containers():
    """List all Docker containers and their volumes"""
    
    if not docker_manager.is_connected:
        raise HTTPException(
            status_code=503,
            detail="Docker not connected. Check docker socket mount."
        )
    
    containers = docker_manager.get_containers()
    
    return {
        "containers": containers,
        "total": len(containers),
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/analyze")
async def analyze_paths():
    """Analyze current path configuration and detect conflicts"""
    
    if not docker_manager.is_connected:
        raise HTTPException(
            status_code=503,
            detail="Docker not connected. Check docker socket mount."
        )
    
    containers = docker_manager.get_containers()
    
    if not containers:
        return {
            "error": "No containers found",
            "status": "no_data"
        }
    
    analyzer = PathAnalyzer(containers)
    analysis = analyzer.analyze()
    
    logger.info(f"Analysis complete: {analysis['summary']}")
    
    return analysis

@app.get("/api/recommendations")
async def get_recommendations():
    """Get recommendations for current setup"""
    
    if not docker_manager.is_connected:
        return {
            "recommendations": [
                {
                    "priority": "critical",
                    "title": "Connect Docker Socket",
                    "description": "MapArr needs access to Docker to analyze your setup",
                    "action": "Mount /var/run/docker.sock in compose file"
                }
            ]
        }
    
    containers = docker_manager.get_containers()
    analyzer = PathAnalyzer(containers)
    analysis = analyzer.analyze()
    
    return {
        "platform": analysis['platform'],
        "recommendations": analysis['recommendations'],
        "conflicts": analysis['conflicts']
    }

@app.post("/api/save-mapping")
async def save_mapping(mapping: Dict[str, Any]):
    """Save user's path mapping decisions"""
    
    # TODO: Implement persistence (SQLite or JSON)
    logger.info(f"Saving mapping: {mapping}")
    
    return {
        "status": "saved",
        "mapping": mapping,
        "timestamp": datetime.now().isoformat()
    }

# ═══════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ═══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """App startup"""
    logger.info("🚀 MapArr v1.0 starting up...")
    logger.info(f"Docker: {docker_manager.connection_method if docker_manager.is_connected else 'NOT CONNECTED'}")

@app.on_event("shutdown")
async def shutdown_event():
    """App shutdown"""
    logger.info("🛑 MapArr shutting down...")
    if docker_manager.client:
        docker_manager.client.close()

# ═══════════════════════════════════════════════════════════
# LOCAL DEVELOPMENT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9900,
        log_level="info"
    )

# ═══════════════════════════════════════════════════════════
# [2026-02-13] MapArr v1.0 backend foundation
# Docker detection + path analysis + conflict detection
# Ready for frontend integration
# ═══════════════════════════════════════════════════════════
