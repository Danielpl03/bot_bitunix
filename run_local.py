# run_local.py
from pathlib import Path
from dotenv import load_dotenv

# Carga .env desde la misma carpeta que este script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Ahora importamos el bot
import bot

if __name__ == "__main__":
    bot.main()