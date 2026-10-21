"""Plantillas de email en texto plano. Claves: plantilla → idioma → (asunto, cuerpo)."""

from string import Template

TEMPLATES: dict[str, dict[str, tuple[str, str]]] = {
    "verify_email": {
        "es": (
            "Confirma tu email en Metting Agent",
            "Hola $name:\n\nConfirma tu dirección de email abriendo este enlace:\n\n$link\n\n"
            "El enlace caduca en 24 horas. Si no has creado una cuenta, ignora este mensaje.\n",
        ),
        "ca": (
            "Confirma el teu correu a Metting Agent",
            "Hola $name:\n\nConfirma la teva adreça de correu obrint aquest enllaç:\n\n$link\n\n"
            "L'enllaç caduca en 24 hores. Si no has creat cap compte, ignora aquest missatge.\n",
        ),
    },
    "password_reset": {
        "es": (
            "Restablece tu contraseña de Metting Agent",
            "Hola $name:\n\nPara elegir una contraseña nueva, abre este enlace:\n\n$link\n\n"
            "El enlace caduca en 1 hora y solo puede usarse una vez. "
            "Si no lo has pedido tú, ignora este mensaje.\n",
        ),
        "ca": (
            "Restableix la contrasenya de Metting Agent",
            "Hola $name:\n\nPer triar una contrasenya nova, obre aquest enllaç:\n\n$link\n\n"
            "L'enllaç caduca en 1 hora i només es pot fer servir una vegada. "
            "Si no ho has demanat tu, ignora aquest missatge.\n",
        ),
    },
    "account_exists": {
        "es": (
            "Intento de registro en Metting Agent",
            "Hola $name:\n\nAlguien ha intentado crear una cuenta con este email, "
            "pero ya tienes una.\n"
            "Si has olvidado la contraseña, puedes restablecerla aquí:\n\n$link\n",
        ),
        "ca": (
            "Intent de registre a Metting Agent",
            "Hola $name:\n\nAlgú ha intentat crear un compte amb aquest correu, "
            "però ja en tens un.\n"
            "Si has oblidat la contrasenya, la pots restablir aquí:\n\n$link\n",
        ),
    },
    "invitation": {
        "es": (
            "Invitación a $organization en Metting Agent",
            "Hola:\n\n$inviter te ha invitado a unirte a «$organization» con el rol $role.\n\n"
            "Acepta la invitación aquí:\n\n$link\n\nLa invitación caduca en 7 días.\n",
        ),
        "ca": (
            "Invitació a $organization a Metting Agent",
            "Hola:\n\n$inviter t'ha convidat a unir-te a «$organization» amb el rol $role.\n\n"
            "Accepta la invitació aquí:\n\n$link\n\nLa invitació caduca en 7 dies.\n",
        ),
    },
}

DEFAULT_LOCALE = "es"


def render(template: str, locale: str, **context: str) -> tuple[str, str]:
    variants = TEMPLATES[template]
    subject, body = variants.get(locale) or variants[DEFAULT_LOCALE]
    return Template(subject).substitute(context), Template(body).substitute(context)
