"""Contexto estático (CAG): estimaciones históricas que se inyectan en el system prompt.

Cada ejemplo se define con datos estructurados y la estimación en Markdown se genera a
partir de ellos, así los totales de horas y costes siempre cuadran con el desglose.
Funcionan como los few-shot examples del prompt: cuanto más representativos, mejor output.
"""

from dataclasses import dataclass
from typing import Literal

Role = Literal["dev", "design"]

# Tarifas de referencia (EUR/hora)
HOURLY_RATES: dict[Role, float] = {"dev": 62.50, "design": 50.00}


@dataclass(frozen=True)
class Task:
    name: str
    role: Role
    hours: int

    @property
    def cost(self) -> float:
        return self.hours * HOURLY_RATES[self.role]


@dataclass(frozen=True)
class CanonicalExample:
    title: str
    meeting_summary: str
    tasks: tuple[Task, ...]
    team: str
    duration: str

    @property
    def total_hours(self) -> int:
        return sum(task.hours for task in self.tasks)

    @property
    def total_cost(self) -> float:
        return sum(task.cost for task in self.tasks)


def _eur(amount: float) -> str:
    """Formatea un importe en euros con separador de miles español: 1.234,50 €."""
    return f"{amount:,.2f} €".replace(",", "_").replace(".", ",").replace("_", ".")


def render_estimation(example: CanonicalExample) -> str:
    """Genera la estimación en Markdown a partir de los datos estructurados."""
    rows = "\n".join(
        f"| {task.name} | {'Diseño' if task.role == 'design' else 'Desarrollo'} "
        f"| {task.hours} | {_eur(task.cost)} |"
        for task in example.tasks
    )
    return (
        f"## Estimación: {example.title}\n\n"
        "### Desglose de tareas\n\n"
        "| Tarea | Perfil | Horas | Coste |\n"
        "|---|---|---:|---:|\n"
        f"{rows}\n\n"
        f"**Total estimado: {example.total_hours} horas**\n"
        f"**Coste total: {_eur(example.total_cost)}**\n"
        f"**Equipo recomendado: {example.team}**\n"
        f"**Duración estimada: {example.duration}**"
    )


CANONICAL_EXAMPLES: list[CanonicalExample] = [
    CanonicalExample(
        title="Plataforma de Gestión de Inventario",
        meeting_summary=(
            "El cliente, una distribuidora de material de oficina con 3 almacenes, necesita "
            "una plataforma web de gestión de inventario: alta, edición y baja de productos, "
            "control de stock por almacén, usuarios con roles (administrador y operario) y un "
            "dashboard con métricas de rotación y alertas de stock bajo."
        ),
        tasks=(
            Task("Diseño UI/UX", "design", 40),
            Task("Backend API (CRUD de inventario y stock por almacén)", "dev", 60),
            Task("Autenticación y roles", "dev", 20),
            Task("Dashboard con métricas y alertas", "dev", 30),
            Task("Testing y QA", "dev", 25),
        ),
        team="2 desarrolladores full-stack + 1 diseñador UX (part-time)",
        duration="6-8 semanas",
    ),
    CanonicalExample(
        title="App Móvil de Reservas para Gimnasio",
        meeting_summary=(
            "Una cadena de 5 gimnasios quiere una app móvil (iOS y Android) para que sus "
            "socios reserven clases dirigidas, paguen la cuota mensual con tarjeta y reciban "
            "notificaciones de recordatorio. Los monitores necesitan un panel web sencillo "
            "para publicar horarios y ver la ocupación de cada clase."
        ),
        tasks=(
            Task("Diseño UI/UX de app y panel web", "design", 60),
            Task("App multiplataforma (Flutter): registro, horarios y reservas", "dev", 120),
            Task("Backend API: socios, clases, plazas y reservas", "dev", 80),
            Task("Pagos recurrentes con Stripe", "dev", 40),
            Task("Notificaciones push de recordatorio", "dev", 20),
            Task("Panel web para monitores", "dev", 50),
            Task("Testing, QA y publicación en las stores", "dev", 40),
        ),
        team="2 desarrolladores (1 móvil + 1 backend) + 1 diseñador UX (part-time)",
        duration="10-12 semanas",
    ),
    CanonicalExample(
        title="Web Corporativa con Blog y Newsletter",
        meeting_summary=(
            "Un despacho de abogados quiere renovar su web corporativa: 6 páginas "
            "institucionales, un blog que el equipo pueda actualizar sin conocimientos "
            "técnicos, formulario de contacto y alta en la newsletter conectada con "
            "Mailchimp. Ya tienen logotipo y guía de estilo, pero no diseño de la web."
        ),
        tasks=(
            Task("Diseño de la web a partir de la guía de estilo", "design", 30),
            Task("Maquetación responsive de las páginas institucionales", "dev", 30),
            Task("Configuración del CMS y blog con editor visual", "dev", 20),
            Task("Formulario de contacto e integración con Mailchimp", "dev", 12),
            Task("SEO técnico, rendimiento y analítica", "dev", 8),
            Task("Testing, QA y puesta en producción", "dev", 10),
        ),
        team="1 desarrollador web + 1 diseñador (part-time)",
        duration="4-5 semanas",
    ),
]

# Formato del enunciado: lista de dicts con el resumen y la estimación ya renderizada
ESTIMATION_EXAMPLES: list[dict[str, str]] = [
    {"meeting_summary": example.meeting_summary, "estimation": render_estimation(example)}
    for example in CANONICAL_EXAMPLES
]


def format_examples_for_prompt(examples: list[dict[str, str]] = ESTIMATION_EXAMPLES) -> str:
    """Convierte los ejemplos en el bloque de texto que se inyecta en el system prompt."""
    blocks = [
        f"### Ejemplo {i}\n\n"
        f"**Resumen de la reunión:**\n{example['meeting_summary']}\n\n"
        f"**Estimación entregada:**\n\n{example['estimation']}"
        for i, example in enumerate(examples, start=1)
    ]
    return "\n\n---\n\n".join(blocks)
