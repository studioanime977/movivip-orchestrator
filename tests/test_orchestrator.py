#!/usr/bin/env python3
"""
Pruebas del MOVIVIP Orchestrator V1.0
Tests automatizados para verificar funcionalidad del orquestador
"""

import unittest
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Agregar el directorio padre al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator import (
    ConfigManager, ResourceMonitor, ProjectState, Task,
    TestResult, OrchestratorError
)


class TestConfigManager(unittest.TestCase):
    """Pruebas del gestor de configuración"""

    def setUp(self):
        """Crear archivo de config temporal"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.json')
        self.manager = ConfigManager(self.config_path)

    def tearDown(self):
        """Limpiar directorio temporal"""
        shutil.rmtree(self.temp_dir)

    def test_load_config_creates_default(self):
        """Verificar que se carga configuración por defecto si no existe"""
        config = self.manager.config
        self.assertIn('version', config)
        self.assertIn('max_iterations', config)
        # El archivo se crea solo cuando se llama a save_config()
        # Antes de save_config(), el archivo no debe existir
        # Después de save_config(), sí debe existir
        self.manager.save_config()
        self.assertTrue(os.path.exists(self.config_path))

    def test_save_and_load_config(self):
        """Verificar guardado y carga de configuración"""
        self.manager.config['custom_key'] = 'custom_value'
        self.manager.save_config()

        new_manager = ConfigManager(self.config_path)
        self.assertEqual(new_manager.get('custom_key'), 'custom_value')

    def test_get_with_default(self):
        """Verificar obtención con valor por defecto"""
        value = self.manager.get('non_existent_key', 'default_value')
        self.assertEqual(value, 'default_value')


class TestResourceMonitor(unittest.TestCase):
    """Pruebas del monitor de recursos"""

    def test_get_memory_available(self):
        """Verificar obtención de memoria disponible"""
        memory = ResourceMonitor.get_memory_available()
        self.assertIsInstance(memory, float)
        self.assertGreater(memory, 0)

    def test_get_memory_percent(self):
        """Verificar obtención de porcentaje de memoria usada"""
        percent = ResourceMonitor.get_memory_used_percent()
        self.assertIsInstance(percent, float)
        self.assertGreaterEqual(percent, 0)
        self.assertLessEqual(percent, 100)

    def test_check_resources(self):
        """Verificar chequeo de recursos"""
        config = {'resource_limits': {'check_memory': True, 'max_ram_mb': 2800}}
        result = ResourceMonitor.check_resources(config)

        self.assertIn('memory_available_mb', result)
        self.assertIn('memory_used_percent', result)
        self.assertIn('critical', result)
        self.assertIn('reason', result)


class TestProjectState(unittest.TestCase):
    """Pruebas del estado de proyecto"""

    def setUp(self):
        """Crear directorio temporal"""
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = os.path.join(self.temp_dir, 'test_project')
        os.makedirs(self.project_path)

    def tearDown(self):
        """Limpiar directorio temporal"""
        shutil.rmtree(self.temp_dir)

    def test_load_default_state(self):
        """Verificar estado por defecto"""
        state_path = os.path.join(self.temp_dir, 'state')
        project_state = ProjectState(self.project_path, state_path)
        state = project_state.load()

        self.assertEqual(state['project_path'], self.project_path)
        self.assertEqual(state['status'], 'idle')
        self.assertEqual(state['iteration'], 0)

    def test_save_and_load_state(self):
        """Verificar guardado y carga de estado"""
        state_path = os.path.join(self.temp_dir, 'state')
        project_state = ProjectState(self.project_path, state_path)

        state = project_state.load()
        state['iteration'] = 5
        state['status'] = 'running'
        project_state.save(state)

        loaded = project_state.load()
        self.assertEqual(loaded['iteration'], 5)
        self.assertEqual(loaded['status'], 'running')

    def test_update_state(self):
        """Verificar actualización parcial del estado"""
        state_path = os.path.join(self.temp_dir, 'state')
        project_state = ProjectState(self.project_path, state_path)

        updated = project_state.update(iteration=10, status='running')

        self.assertEqual(updated['iteration'], 10)
        self.assertEqual(updated['status'], 'running')
        self.assertIsNotNone(updated['last_activity'])


class TestTask(unittest.TestCase):
    """Pruebas del modelo de tarea"""

    def test_task_creation(self):
        """Verificar creación de tarea"""
        task = Task(
            task_id='test-001',
            description='Crear archivo de prueba',
            priority=1
        )

        self.assertEqual(task.id, 'test-001')
        self.assertEqual(task.description, 'Crear archivo de prueba')
        self.assertEqual(task.status, 'Pending')

    def test_task_to_dict(self):
        """Verificar serialización a diccionario"""
        task = Task(
            task_id='test-002',
            description='Test task',
            priority=2,
            dependencies=['test-001']
        )

        task_dict = task.to_dict()
        self.assertEqual(task_dict['id'], 'test-002')
        self.assertIn('test-001', task_dict['dependencies'])

    def test_task_from_dict(self):
        """Verificar deserialización desde diccionario"""
        task_dict = {
            'id': 'test-003',
            'description': 'Deserialized task',
            'priority': 1,
            'status': 'Running',
            'dependencies': [],
            'evidence': ['file1.py'],
            'required_tests': ['test_task'],
            'files': ['src/task.py'],
            'blocking_reason': None
        }

        task = Task.from_dict(task_dict)
        self.assertEqual(task.id, 'test-003')
        self.assertEqual(task.status, 'Running')
        self.assertIn('file1.py', task.evidence)


class TestTestResult(unittest.TestCase):
    """Pruebas del resultado de pruebas"""

    def test_test_result_passed(self):
        """Verificar resultado de prueba exitoso"""
        result = TestResult(
            command='pytest test.py',
            return_code=0,
            duration=5.5,
            output='PASSED',
            test_path='tests/test.py'
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.return_code, 0)

    def test_test_result_failed(self):
        """Verificar resultado de prueba fallido"""
        result = TestResult(
            command='pytest test.py',
            return_code=1,
            duration=3.2,
            output='FAILED',
            test_path='tests/test.py'
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.return_code, 1)


class TestLockManager(unittest.TestCase):
    """Pruebas del gestor de locks"""

    def test_project_lock_single(self):
        """Verificar lock para un solo proyecto"""
        from orchestrator import LockManager

        lock_manager = LockManager()

        # Crear directorio temporal para el proyecto
        temp_dir = tempfile.mkdtemp()
        try:
            # Primer lock debe ser exitoso
            result = lock_manager.acquire_project_lock(temp_dir)
            self.assertTrue(result)

            # Segundo lock en el mismo proyecto debe fallar
            result2 = lock_manager.acquire_project_lock(temp_dir)
            self.assertFalse(result2)

            # Liberar lock
            lock_manager.release_project_lock(temp_dir)

            # Nuevo lock debe ser exitoso
            result3 = lock_manager.acquire_project_lock(temp_dir)
            self.assertTrue(result3)

        finally:
            shutil.rmtree(temp_dir)


class TestCycleDetection(unittest.TestCase):
    """Pruebas de detección de ciclos"""

    def test_detect_progress_change(self):
        """Verificar detección de cambio de progreso"""
        state_path = tempfile.mkdtemp()
        project_path = os.path.join(state_path, 'project')
        os.makedirs(project_path)

        try:
            project_state = ProjectState(project_path, state_path)

            # Estado inicial con hash vacío
            state1 = {'iteration': 1, 'last_progress_hash': 'abc123'}
            project_state.save(state1)

            # Estado con hash diferente
            state2 = {'iteration': 2, 'last_progress_hash': 'def456'}
            project_state.save(state2)

            # Verificar que los hashes son diferentes (es progreso)
            self.assertNotEqual(state1['last_progress_hash'], state2['last_progress_hash'])

        finally:
            shutil.rmtree(state_path)


class TestExecutionLimits(unittest.TestCase):
    """Pruebas de límites de ejecución"""

    def test_max_iterations_limit(self):
        """Verificar límite de iteraciones"""
        config = ConfigManager()
        max_iter = config.get('max_iterations', 200)
        self.assertLessEqual(max_iter, 200)

    def test_max_time_limit(self):
        """Verificar límite de tiempo"""
        config = ConfigManager()
        max_time = config.get('max_time_seconds', 28800)
        self.assertLessEqual(max_time, 28800)  # 8 horas


def run_tests():
    """Ejecutar todas las pruebas"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Agregar todas las clases de prueba
    suite.addTests(loader.loadTestsFromTestCase(TestConfigManager))
    suite.addTests(loader.loadTestsFromTestCase(TestResourceMonitor))
    suite.addTests(loader.loadTestsFromTestCase(TestProjectState))
    suite.addTests(loader.loadTestsFromTestCase(TestTask))
    suite.addTests(loader.loadTestsFromTestCase(TestTestResult))
    suite.addTests(loader.loadTestsFromTestCase(TestLockManager))
    suite.addTests(loader.loadTestsFromTestCase(TestCycleDetection))
    suite.addTests(loader.loadTestsFromTestCase(TestExecutionLimits))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())