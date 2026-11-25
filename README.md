# MOVIVIP Autonomous Development Orchestrator V1.0

## Instalación y Documentación

### Requisitos previos
- Python 3.12+
- Claude Code CLI 2.1.332+
- Git
- ~17 GiB RAM disponibles

### Instalación Global

```bash
# 1. Crear estructura de directorios
mkdir -p ~/.movivip-orchestrator/{config,prompts,projects,sessions,logs,state,tests,backups}

# 2. Instalar ejecutable
chmod +x ~/.movivip-orchestrator/orchestrator.py
ln -sf ~/.movivip-orchestrator/orchestrator.py ~/.local/bin/moviauto

# 3. Verificar instalación
moviauto doctor

# 4. Opcional: Agregar PATH si es necesario
export PATH="$HOME/.local/bin:$PATH"
```

### Comandos Principales

```bash
# Ver ayuda
moviauto --help

# Verificar salud del sistema
moviauto doctor

# Mostrar configuración
moviauto config

# Listar proyectos
moviauto projects

# Iniciar orquestación
moviauto start

# Ver logs
moviauto logs -n 50

# Generar reporte
moviauto report
```

### Perfiles de Proyecto

El orquestador incluye perfiles predefinidos:

- **movivpn**: Gradle/Android con motores SSH (GO)
- **android_gradle**: Compilación y pruebas Android
- **python**: Compilación y pruebas Python
- **go**: Compilación y pruebas Go
- **nodejs**: Compilación y pruebas Node.js

### Flujo de Trabajo Automativo

1. `moviauto start` - Inicia la ejecución automática
2. El orquestador carga configuración y proyecto
3. Verifica límites (200 iteraciones, 8 horas)
4. Ejecuta Claude Code con prompts de continuación
5. Compila y ejecuta pruebas automáticamente
6. Detecta cambios reales y ciclos sin progreso
7. Detiene automáticamente después de 3 ciclos sin progreso
8. Guarda estado persistente en `~/.movivip-orchestrator/state/`

### Sistema Anti-Loop

- Detecta 3 ciclos consecutivos sin progreso verificable
- Detiene la ejecución y registra motivo
- No cuenta cambios cosméticos o de logs como progreso
- Reconoce pruebas nuevas y bloqueos técnicos como progreso

### Licencia

MIT License - Ver archivo LICENSE para más detalles.

### Contacto

- GitHub: @movivipnetwork
- Email: support@movivip.net