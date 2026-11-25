#!/usr/bin/env python3
"""
MOVIVIP Autonomous Development Orchestrator V1.0
Orquestador global de desarrollo autónomo para Claude Code
"""

import argparse
import json
import os
import subprocess
import sys
import time
import hashlib
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import threading
import signal
import glob

# Constantes
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.movivip-orchestrator/config.json")
DEFAULT_PROJECTS_PATH = os.path.expanduser("~/.movivip-orchestrator/projects")
LOCK_FILE_EXT = ".moviauto.lock"

# Archivos excluidos del detección de cambios
EXCLUDED_PATTERNS = [
    '.git/', '.gradle/', 'build/', 'app/build/', 'node_modules/',
    '__pycache__/', '.venv/', '*.class', '*.dex', '*.hprof',
    '*.tmp', '*.log', '*.cache', 'gradle/', '*.gradle*',
]

# Estado persistente global
global_state = {
    'running': False,
    'current_project': None,
    'start_time': None
}


class OrchestratorError(Exception):
    """Excepción personalizada para el orquestador"""
    pass


class ProjectLock:
    """Manejo de locks para proyectos"""

    def __init__(self, project_path: str, lock_manager: 'LockManager'):
        self.project_path = project_path
        self.lock_manager = lock_manager
        self.locked = False

    def acquire(self) -> bool:
        return self.lock_manager.acquire_project_lock(self.project_path)

    def release(self):
        self.lock_manager.release_project_lock(self.project_path)


class LockManager:
    """Gestor central de locks por proyecto"""

    def __init__(self):
        self.locks = {}
        self.lock = threading.Lock()

    def acquire_project_lock(self, project_path: str) -> bool:
        with self.lock:
            if project_path in self.locks:
                return False
            lock_file = Path(project_path) / LOCK_FILE_EXT
            try:
                lock_file.touch(exist_ok=False)
                self.locks[project_path] = lock_file
                return True
            except FileExistsError:
                return False

    def release_project_lock(self, project_path: str):
        with self.lock:
            if project_path in self.locks:
                lock_file = self.locks.pop(project_path)
                try:
                    lock_file.unlink(missing_ok=True)
                except:
                    pass


class ConfigManager:
    """Gestor de configuración"""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return json.load(f)
        return self._default_config()

    def _default_config(self) -> Dict:
        return {
            "version": "1.0.0",
            "max_iterations": 200,
            "max_time_seconds": 28800,  # 8 horas
            "auto_continue": True,
            "auto_compile": True,
            "auto_test": True,
            "session_recovery": True,
            "multiproject": True,
            "persistent_log": True,
            "cycle_detection": True,
            "respect_permissions": True,
            "default_model": "claude-opus-5-5",
            "claude_path": "/home/movivipnetwork/.local/bin/claude",
            "exclusions": [".git/", ".gradle/", "build/", "node_modules/",
                          "__pycache__/", ".venv/", "*.class", "*.dex"],
            "profiles": {}
        }

    def save_config(self):
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)

    def get(self, key: str, default=None):
        return self.config.get(key, default)


class ResourceMonitor:
    """Monitoreo de recursos del sistema"""

    @staticmethod
    def get_memory_available() -> int:
        return psutil.virtual_memory().available / (1024 * 1024)  # MB

    @staticmethod
    def get_memory_used_percent() -> float:
        return psutil.virtual_memory().percent

    @staticmethod
    def get_cpu_percent() -> float:
        return psutil.cpu_percent(interval=0.1)

    @staticmethod
    def check_resources(config: Dict) -> Dict:
        limits = config.get('resource_limits', {})
        available_memory = ResourceMonitor.get_memory_available()
        cpu_usage = ResourceMonitor.get_cpu_percent()

        result = {
            'memory_available_mb': available_memory,
            'memory_used_percent': ResourceMonitor.get_memory_used_percent(),
            'cpu_percent': cpu_usage,
            'critical': False,
            'reason': None
        }

        if limits.get('check_memory', True):
            max_ram = limits.get('max_ram_mb', 2800)
            if available_memory < max_ram:
                result['critical'] = True
                result['reason'] = f"Memoria disponible ({available_memory:.0f}MB) por debajo del límite ({max_ram}MB)"

        return result


class ClaudeWrapper:
    """Wrapper para comandos de Claude Code"""

    def __init__(self, config_manager: ConfigManager):
        self.claude_path = config_manager.get('claude_path', 'claude')
        self.default_model = config_manager.get('default_model', 'claude-opus-5-5')

    def run(self, prompt: str, project_path: str, model: Optional[str] = None,
            max_turns: int = 10, timeout: int = 600) -> Dict:
        """Ejecuta Claude Code con las opciones adecuadas"""

        cmd = [
            self.claude_path,
            '-p',  # Non-interactive mode
            '--print',
            '--output-format=json',
            '--permission-mode=plan',
            '-m', model or self.default_model,
            '--max-turns', str(max_turns)
        ]

        env = os.environ.copy()
        env['CLAUDE_PROJECT'] = project_path
        env['CLAUDE_CODE_SIMPLE'] = '1'

        result = {
            'success': False,
            'output': None,
            'error': None,
            'return_code': None,
            'session_context': None
        }

        try:
            process = subprocess.run(
                cmd,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )

            result['return_code'] = process.returncode
            result['success'] = process.returncode == 0
            result['output'] = process.stdout
            result['error'] = process.stderr

            if process.stdout:
                try:
                    result['session_context'] = json.loads(process.stdout)
                except json.JSONDecodeError:
                    result['session_context'] = {'raw_output': process.stdout}

        except subprocess.TimeoutExpired:
            result['error'] = f"Timeout after {timeout} seconds"
        except Exception as e:
            result['error'] = str(e)

        return result


class ProjectState:
    """Manejo del estado persistente de proyectos"""

    def __init__(self, project_path: str, base_path: str):
        self.project_path = project_path
        self.state_dir = Path(base_path) / "state"
        self.state_file = self.state_dir / f"{self._get_project_id()}.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_project_id(self) -> str:
        """Genera un ID único para el proyecto basado en la ruta"""
        return hashlib.md5(self.project_path.encode()).hexdigest()[:12]

    def load(self) -> Dict:
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return self._default_state()

    def _default_state(self) -> Dict:
        return {
            'project_path': self.project_path,
            'project_id': self._get_project_id(),
            'iteration': 0,
            'max_iterations': 200,
            'start_time': None,
            'last_activity': None,
            'current_task': None,
            'last_compile': None,
            'last_tests': None,
            'cycles_no_progress': 0,
            'last_progress_hash': None,
            'status': 'idle',
            'errors': [],
            'tasks': []
        }

    def save(self, state: Dict):
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    def update(self, **kwargs):
        state = self.load()
        state.update(kwargs)
        state['last_activity'] = datetime.now().isoformat()
        self.save(state)
        return state


class Task:
    """Modelo de tarea"""

    def __init__(self, task_id: str, description: str, priority: int = 1,
                 dependencies: List[str] = None, required_tests: List[str] = None,
                 files: List[str] = None, blocked: bool = False):
        self.id = task_id
        self.description = description
        self.priority = priority
        self.dependencies = dependencies or []
        self.required_tests = required_tests or []
        self.files = files or []
        self.status = 'Pending'
        self.evidence = []
        self.blocking_reason = None

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'description': self.description,
            'priority': self.priority,
            'dependencies': self.dependencies,
            'status': self.status,
            'evidence': self.evidence,
            'required_tests': self.required_tests,
            'files': self.files,
            'blocking_reason': self.blocking_reason
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Task':
        task = cls(
            task_id=data.get('id', ''),
            description=data.get('description', ''),
            priority=data.get('priority', 1),
            dependencies=data.get('dependencies', []),
            required_tests=data.get('required_tests', []),
            files=data.get('files', []),
            blocked=data.get('blocking_reason') is not None
        )
        task.status = data.get('status', 'Pending')
        task.evidence = data.get('evidence', [])
        task.blocking_reason = data.get('blocking_reason')
        return task


class TestResult:
    """Resultado de pruebas"""

    def __init__(self, command: str, return_code: int, duration: float,
                 output: str, test_path: str):
        self.command = command
        self.return_code = return_code
        self.duration = duration
        self.output = output
        self.test_path = test_path
        self.timestamp = datetime.now().isoformat()
        self.passed = return_code == 0

    def to_dict(self) -> Dict:
        return {
            'command': self.command,
            'return_code': self.return_code,
            'duration': self.duration,
            'output': self.output[:1000],
            'test_path': self.test_path,
            'timestamp': self.timestamp,
            'passed': self.passed
        }


class ExecutionEngine:
    """Motor de ejecución automático"""

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.project_state = None
        self.claude = ClaudeWrapper(config_manager)
        self.profiles = config_manager.get('profiles', {})
        self.exclusions = EXCLUDED_PATTERNS + config_manager.get('exclusions', [])

    def compute_file_hash(self, project_path: str) -> str:
        """Computa hash de archivos relevantes del proyecto"""
        hasher = hashlib.sha256()

        for root, dirs, files in os.walk(project_path):
            # Excluir directorios
            rel_root = os.path.relpath(root, project_path)
            skip = False
            for pattern in self.exclusions:
                if pattern in rel_root or rel_root.startswith('.') or pattern in files:
                    skip = True
                    break
            if skip:
                continue

            for f in files:
                file_path = os.path.join(root, f)
                try:
                    with open(file_path, 'rb') as fh:
                        hasher.update(fh.read())
                except:
                    pass

        return hasher.hexdigest()

    def get_pending_tasks(self, project_path: str) -> List[Dict]:
        """Obtiene tareas pendientes del archivo TASKS.md"""
        tasks_file = os.path.join(project_path, 'TASKS.md')
        tasks = []

        if os.path.exists(tasks_file):
            try:
                with open(tasks_file, 'r') as f:
                    content = f.read()
                # Parse simple de markdown para tareas
                for line in content.split('\n'):
                    if line.strip().startswith('- [ ]'):
                        task_text = line.strip()[5:].strip()
                        task_id = f"task_{len(tasks)+1}"
                        tasks.append({
                            'id': task_id,
                            'description': task_text,
                            'priority': 1,
                            'status': 'Pending'
                        })
            except:
                pass

        return tasks

    def execute_build(self, project_path: str, profile_name: str = None) -> TestResult:
        """Ejecuta comandos de compilación del proyecto"""
        profile = {}
        if profile_name and profile_name in self.profiles:
            profile = self.profiles[profile_name]

        build_tasks = profile.get('build_tasks', [
            'python3 -m py_compile' if project_path.endswith('.py') else 'echo "build"'
        ])

        start_time = time.time()
        output = ""
        return_code = 0

        for task in build_tasks:
            try:
                result = subprocess.run(
                    task,
                    cwd=project_path,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                output += result.stdout + result.stderr
                return_code = result.returncode
                if return_code != 0:
                    break
            except Exception as e:
                output += str(e)
                return_code = 1
                break

        duration = time.time() - start_time
        return TestResult(
            command=' && '.join(build_tasks),
            return_code=return_code,
            duration=duration,
            output=output,
            test_path=project_path
        )

    def execute_tests(self, project_path: str, profile_name: str = None) -> TestResult:
        """Ejecuta comandos de pruebas del proyecto"""
        profile = {}
        if profile_name and profile_name in self.profiles:
            profile = self.profiles[profile_name]

        test_commands = profile.get('test_commands', [])

        if not test_commands:
            return TestResult(
                command='no tests configured',
                return_code=0,
                duration=0,
                output='No tests configured',
                test_path=project_path
            )

        start_time = time.time()
        output = ""
        return_code = 0

        for cmd in test_commands:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=project_path,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                output += result.stdout + result.stderr
                return_code = result.returncode
                if return_code != 0:
                    break
            except Exception as e:
                output += str(e)
                return_code = 1
                break

        duration = time.time() - start_time
        return TestResult(
            command=' && '.join(test_commands),
            return_code=return_code,
            duration=duration,
            output=output,
            test_path=project_path
        )

    def run_single_iteration(self, project_path: str, iteration: int,
                            profile_name: str = None) -> Dict:
        """Ejecuta una iteración única del motor"""
        # 1. Verificar límites
        if iteration >= self.config.get('max_iterations', 200):
            return {'status': 'completed', 'reason': 'Max iterations reached'}

        # 2. Verificar tiempo
        start_time = global_state.get('start_time')
        if start_time:
            elapsed = (datetime.now().timestamp() - start_time)
            if elapsed >= self.config.get('max_time_seconds', 28800):
                return {'status': 'completed', 'reason': 'Max time reached'}

        # 3. Verificar recursos
        resources = ResourceMonitor.check_resources(self.config.config)
        if resources['critical']:
            return {'status': 'blocked', 'reason': resources['reason']}

        # 4. Computar hash actual
        current_hash = self.compute_file_hash(project_path)

        # 5. Obtener tareas pendientes
        tasks = self.get_pending_tasks(project_path)

        if not tasks:
            return {'status': 'completed', 'reason': 'No pending tasks'}

        # 6. Seleccionar tarea (prioridad alta, luego por orden)
        current_task = tasks[0]
        current_task['status'] = 'Running'

        # 7. Ejecutar Claude con el prompt de continuación
        prompt = """Consulta el roadmap persistente, selecciona la siguiente tarea ejecutable, realiza modificaciones reales cuando correspondan, ejecuta las pruebas y registra evidencias. No repitas tareas completadas sin una regresión demostrada."""

        result = self.claude.run(
            prompt=prompt,
            project_path=project_path,
            max_turns=5,
            timeout=120
        )

        # 8. Verificar cambios
        new_hash = self.compute_file_hash(project_path)
        has_changes = current_hash != new_hash

        # 9. Ejecutar compilación si está configurado
        compile_result = None
        if has_changes and self.config.get('auto_compile', True):
            compile_result = self.execute_build(project_path, profile_name)

        # 10. Ejecutar pruebas si está configurado
        test_result = None
        if compile_result and compile_result.passed and self.config.get('auto_test', True):
            test_result = self.execute_tests(project_path, profile_name)

        # 11. Detección de ciclos
        previous_hash = self.project_state.load().get('last_progress_hash') if self.project_state else None
        cycles_no_progress = 0

        if not has_changes and not compile_result:
            cycles_no_progress = 3  # Marcar como sin progreso
        elif previous_hash == new_hash:
            cycles_no_progress = 3
        else:
            cycles_no_progress = 0

        return {
            'status': 'continue' if cycles_no_progress < 3 else 'blocked',
            'iteration': iteration + 1,
            'task': current_task,
            'has_changes': has_changes,
            'hash_changed': current_hash != new_hash if previous_hash else True,
            'compile_result': compile_result.to_dict() if compile_result else None,
            'test_result': test_result.to_dict() if test_result else None,
            'cycles_no_progress': cycles_no_progress,
            'reason': None if cycles_no_progress < 3 else f'3 ciclos sin progreso detectados',
            'output': result.get('output', ''),
            'session_context': result.get('session_context'),
            'new_hash': new_hash
        }


def main():
    parser = argparse.ArgumentParser(
        description='MOVIVIP Autonomous Development Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Comandos disponibles')

    # start
    start_parser = subparsers.add_parser('start', help='Iniciar orquestación')
    start_parser.add_argument('-p', '--project', help='Ruta del proyecto')
    start_parser.add_argument('-m', '--model', help='Modelo a usar')
    start_parser.add_argument('--max-iterations', type=int, help='Límite de iteraciones')
    start_parser.add_argument('--profile', help='Perfil de proyecto (movivpn, android, python, go, nodejs)')
    start_parser.add_argument('--dry-run', action='store_true', help='No ejecutar Claude, solo mostrar')

    # status
    subparsers.add_parser('status', help='Mostrar estado del orquestador')

    # pause
    subparsers.add_parser('pause', help='Pausar orquestación activa')

    # resume
    subparsers.add_parser('resume', help='Reanudar orquestación pausada')

    # stop
    subparsers.add_parser('stop', help='Detener orquestación')

    # logs
    logs_parser = subparsers.add_parser('logs', help='Ver logs de orquestación')
    logs_parser.add_argument('-n', '--lines', type=int, default=100, help='Número de líneas')
    logs_parser.add_argument('-f', '--follow', action='store_true', help='Seguimiento en tiempo real')

    # config
    subparsers.add_parser('config', help='Mostrar configuración')

    # doctor
    subparsers.add_parser('doctor', help='Verificar salud del sistema')

    # projects
    subparsers.add_parser('projects', help='Listar proyectos')

    # report
    subparsers.add_parser('report', help='Generar reporte del orquestador')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    config_manager = ConfigManager()

    try:
        if args.command == 'start':
            return cmd_start(config_manager, args)
        elif args.command == 'status':
            return cmd_status(config_manager)
        elif args.command == 'pause':
            return cmd_pause(config_manager)
        elif args.command == 'resume':
            return cmd_resume(config_manager)
        elif args.command == 'stop':
            return cmd_stop(config_manager)
        elif args.command == 'logs':
            return cmd_logs(config_manager, args)
        elif args.command == 'config':
            return cmd_config(config_manager)
        elif args.command == 'doctor':
            return cmd_doctor(config_manager)
        elif args.command == 'projects':
            return cmd_projects(config_manager)
        elif args.command == 'report':
            return cmd_report(config_manager)
    except OrchestratorError as e:
        print(f"Error del orquestador: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error inesperado: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def cmd_start(config_manager: ConfigManager, args) -> int:
    """Comando para iniciar orquestación"""

    project_path = args.project or os.getcwd()

    # Verificar recurso
    resources = ResourceMonitor.check_resources(config_manager.config)
    if resources['critical']:
        print(f"Advertencia: {resources['reason']}")
        return 1

    # Crear el directorio del proyecto si no existe
    os.makedirs(project_path, exist_ok=True)

    # Get profile configuration
    profile_name = getattr(args, 'profile', None)

    # Initialize project state
    project_state = ProjectState(project_path, config_manager.get('projects_path', DEFAULT_PROJECTS_PATH))
    state = project_state.load()

    # Check if already running
    if state.get('status') == 'running':
        print(f"El proyecto {project_path} ya está en ejecución")
        print(f"Iteración actual: {state.get('iteration', 0)}")
        return 0

    # Inicializar estado global
    global_state['running'] = True
    global_state['current_project'] = project_path
    global_state['start_time'] = datetime.now().timestamp()

    # Update state
    state.update({
        'status': 'running',
        'start_time': datetime.now().isoformat(),
        'project_path': project_path,
        'max_iterations': args.max_iterations or config_manager.get('max_iterations', 200),
        'current_model': args.model or config_manager.get('default_model'),
        'current_profile': profile_name,
        'iteration': 0,
        'tasks': [],
        'errors': []
    })
    state['last_progress_hash'] = None
    project_state.save(state)

    print(f"Iniciando orquestación del proyecto: {project_path}")
    print(f"Estado: {state['status']}")
    print(f"Modelo: {state.get('current_model', 'default')}")
    print(f"Perfil: {profile_name or 'default'}")

    if getattr(args, 'dry_run', False):
        print("\n[DRY RUN] No se ejecutará Claude - modo simulación")
        return 0

    # Crear TASKS.md si no existe
    tasks_file = os.path.join(project_path, 'TASKS.md')
    if not os.path.exists(tasks_file):
        with open(tasks_file, 'w') as f:
            f.write("# Roadmap de Tareas\n\n")
            f.write("## Tareas pendientes\n\n")
            f.write("- [ ] Analizar el proyecto y generar primer reporte\n")
            f.write("- [ ] Detectar tipos de archivos y estructura\n")
            f.write("- [ ] Configurar perfiles de compilación\n")
            f.write("- [ ] Verificar dependencias del proyecto\n")
            f.write("- [ ] Crear TEST_RESULTS.md inicial\n")

    # Ejecutar motor de ejecución
    engine = ExecutionEngine(config_manager)
    max_iterations = state['max_iterations']
    profile = state.get('current_profile')

    iteration = 0
    while iteration < max_iterations:
        iteration += 1

        print(f"\n{'='*60}")
        print(f"Iteración {iteration}/{max_iterations}")

        result = engine.run_single_iteration(project_path, iteration, profile)

        # Actualizar estado
        state['iteration'] = iteration
        state['last_progress_hash'] = result.get('new_hash')
        state['cycles_no_progress'] = result.get('cycles_no_progress', 0)

        if result.get('compile_result'):
            state['last_compile'] = result['compile_result']

        if result.get('test_result'):
            state['last_tests'] = result['test_result']

        if result.get('status') == 'completed':
            state['status'] = 'completed'
            print(f"\n✓ Estado: {result['reason']}")
            break
        elif result.get('status') == 'blocked':
            state['status'] = 'blocked'
            state['errors'].append(result.get('reason', 'Unknown error'))
            print(f"\n✗ Estado: Bloqueado - {result['reason']}")
            break

        # Progreso verificado
        if result.get('has_changes') or result.get('compile_result', {}).get('passed'):
            print(f"✓ Progreso verificado (ciclos sin progreso: {result.get('cycles_no_progress', 0)})")

        # Actualizar archivo de estado
        project_state.save(state)

    # Estado final
    final_state = project_state.load()
    if final_state['status'] == 'running':
        final_state['status'] = 'completed'
        final_state['iteration'] = iteration
        project_state.save(final_state)

    print(f"\n{'='*60}")
    print(f"Ejecución completada en {iteration} iteraciones")
    print(f"Estado final: {final_state['status']}")

    return 0


def cmd_status(config_manager: ConfigManager) -> int:
    """Comando para mostrar estado"""
    config = config_manager.config

    print("=" * 60)
    print("MOVIVIP Autonomous Development Orchestrator")
    print("=" * 60)
    print(f"\nVersión: {config.get('version', 'unknown')}")
    print(f"Ruta de config: {config_manager.config_path}")

    # Estado de proyectos
    state_path = config_manager.get('state_path', Path.home() / '.movivip-orchestrator' / 'state')
    state_dir = Path(state_path)

    if state_dir.exists():
        state_files = list(state_dir.glob('*.json'))
        if state_files:
            print(f"\nProyectos con estado: {len(state_files)}")
            for sf in state_files[:10]:  # Mostrar hasta 10
                with open(sf, 'r') as f:
                    state = json.load(f)
                status = state.get('status', 'unknown')
                project = state.get('project_path', 'unknown')
                iteration = state.get('iteration', 0)
                cycles = state.get('cycles_no_progress', 0)
                start_time = state.get('start_time', 'N/A')
                print(f"  - {project}")
                print(f"    Estado: {status}, Iteración: {iteration}, Ciclos sin progreso: {cycles}")
                print(f"    Iniciado: {start_time}")

    # Recursos del sistema
    resources = ResourceMonitor.check_resources(config)
    print(f"\nRecursos del sistema:")
    print(f"  Memoria disponible: {resources['memory_available_mb']:.0f} MB")
    print(f"  CPU: {resources['cpu_percent']:.1f}%")

    return 0


def cmd_pause(config_manager: ConfigManager) -> int:
    """Pausar orquestación"""
    global global_state
    global_state['running'] = False
    print("Orquestación pausada")
    return 0


def cmd_resume(config_manager: ConfigManager) -> int:
    """Reanudar orquestación"""
    global global_state
    global_state['running'] = True
    print("Orquestación reanudada")
    return 0


def cmd_stop(config_manager: ConfigManager) -> int:
    """Detener orquestación"""
    global global_state
    global_state['running'] = False
    print("Orquestación detenida")
    return 0


def cmd_logs(config_manager: ConfigManager, args) -> int:
    """Ver logs"""
    log_path = config_manager.get('logs_path', Path.home() / '.movivip-orchestrator' / 'logs')
    log_file = Path(log_path) / 'orchestrator.log'

    if log_file.exists():
        with open(log_file, 'r') as f:
            lines = f.readlines()
            for line in lines[-args.lines:]:
                print(line.rstrip())
    else:
        print("No hay logs disponibles")

    return 0


def cmd_config(config_manager: ConfigManager) -> int:
    """Mostrar configuración"""
    import json
    print(json.dumps(config_manager.config, indent=2))
    return 0


def cmd_doctor(config_manager: ConfigManager) -> int:
    """Verificar salud del sistema"""
    print("Verificando salud del sistema...")

    issues = []

    # Verificar ruta de claude
    claude_path = config_manager.get('claude_path', 'claude')
    if not os.path.exists(claude_path):
        issues.append(f"Claude no encontrado en: {claude_path}")
    else:
        print(f"✓ Claude encontrado: {claude_path}")

    # Verificar Python
    print(f"✓ Python: {sys.version.split()[0]}")

    # Verificar directorios
    dirs = [
        ('config', config_manager.config_path),
        ('logs', config_manager.get('logs_path')),
        ('state', config_manager.get('state_path')),
        ('projects', config_manager.get('projects_path', DEFAULT_PROJECTS_PATH))
    ]

    for name, path in dirs:
        p = Path(path)
        if p.exists():
            print(f"✓ Directorio {name}: {path}")
        else:
            issues.append(f"Directorio {name} no existe: {path}")

    # Verificar recursos
    resources = ResourceMonitor.check_resources(config_manager.config)
    if resources['critical']:
        issues.append(f"Problemas de recursos: {resources['reason']}")
    else:
        print(f"✓ Recursos OK (memoria disponible: {resources['memory_available_mb']:.0f}MB)")

    if issues:
        print("\nProblemas encontrados:")
        for issue in issues:
            print(f"  ✗ {issue}")
        return 1

    print("\n✓ Sistema saludable")
    return 0


def cmd_projects(config_manager: ConfigManager) -> int:
    """Listar proyectos"""
    state_path = config_manager.get('state_path', Path.home() / '.movivip-orchestrator' / 'state')
    state_dir = Path(state_path)

    if not state_dir.exists():
        print("No hay proyectos registrados")
        return 0

    state_files = list(state_dir.glob('*.json'))
    if not state_files:
        print("No hay proyectos registrados")
        return 0

    print("Proyectos registrados:")
    for sf in state_files:
        with open(sf, 'r') as f:
            state = json.load(f)
        project = state.get('project_path', 'unknown')
        status = state.get('status', 'unknown')
        iteration = state.get('iteration', 0)
        print(f"  {project}: {status} (iteración {iteration})")

    return 0


def cmd_report(config_manager: ConfigManager) -> int:
    """Generar reporte del orquestador"""
    print("Generando reporte...")

    # Recopilar información del sistema
    config = config_manager.config
    state_path = config_manager.get('state_path', Path.home() / '.movivip-orchestrator' / 'state')

    report = {
        'timestamp': datetime.now().isoformat(),
        'version': config.get('version', 'unknown'),
        'max_iterations': config.get('max_iterations', 200),
        'max_time_seconds': config.get('max_time_seconds', 28800),
        'profiles_configured': list(config.get('profiles', {}).keys()),
        'auto_continue': config.get('auto_continue', True),
        'auto_compile': config.get('auto_compile', True),
        'auto_test': config.get('auto_test', True),
        'resources': ResourceMonitor.check_resources(config)
    }

    # Estado de proyectos
    state_dir = Path(state_path)
    if state_dir.exists():
        state_files = list(state_dir.glob('*.json'))
        report['projects_count'] = len(state_files)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())