# -*- coding: utf-8 -*-

import logging
import os
import platform
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pyfiglet

# --- Configuration & Helpers ---

class Config:
    """
    Centralise la configuration, les chemins et la logique dépendante de la plateforme.
    """
    def __init__(self):
        self.current_platform = platform.system()
        self._configure_paths()

    def _configure_paths(self):
        """Définit les chemins spécifiques à l'OS."""
        if self.current_platform == 'Windows':
            app_data_root = Path(os.getenv('APPDATA', ''))
            self.file_extension = ".exe"
        elif self.current_platform == 'Linux':
            app_data_root = Path.home() / ".local" / "share"
            self.file_extension = ""
        elif self.current_platform == 'Darwin':
            app_data_root = Path.home() / "Library" / "Application Support"
            self.file_extension = ""
        else:
            raise Exception(f"OS non supporté: {self.current_platform}")

        self.fankai_app_path = app_data_root / 'fankai'
        self.log_path = self.fankai_app_path / 'logs'
        self.db_path = self.fankai_app_path / 'fankai.db'
        self.setup_path = self.fankai_app_path / 'setup'

    def get_placement_executable_path(self):
        """Retourne le chemin de l'exécutable Fankai-Placement."""
        exe_name = f"Fankai-Placement{self.file_extension}"
        return self.setup_path / exe_name

    def ensure_dirs_exist(self):
        """Crée les répertoires nécessaires."""
        self.fankai_app_path.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)


def setup_logging(log_path):
    """Configure le logging pour la console et un fichier."""
    logfile = log_path / 'fankai_placement_scheduler.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(logfile, 'w', 'utf-8'),
            logging.StreamHandler(sys.stdout)
        ])

    sys.excepthook = lambda exc_type, exc_value, exc_traceback: \
        logging.critical("Exception non interceptée", exc_info=(exc_type, exc_value, exc_traceback))


# --- Classes Métier ---

class DatabaseManager:
    """Gère toutes les opérations sur la base de données SQLite."""
    def __init__(self, db_path):
        self.db_path = db_path

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def load_config(self):
        """Charge l'ensemble de la configuration."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM config")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def verify_prerequisites(self):
        """Vérifie que toutes les configurations nécessaires au placement auto sont présentes."""
        config = self.load_config()
        required_keys = {
            'fankai_parents': 'FANKAI_PARENTS',
            'fankai_telechargement': 'FANKAI_TELECHARGEMENT',
            'type_placement': 'TYPE_PLACEMENT',
            'plex_plugin': 'PLEX_PLUGIN'
        }

        missing = []
        for key, default_value in required_keys.items():
            value = config.get(key)
            if not value or value == default_value:
                missing.append(key)

        return missing


class SchedulerManager:
    """Gère la création et la vérification des tâches planifiées pour le placement."""

    TASK_NAME_WINDOWS = "FankaiPlacementDaily"
    TASK_COMMENT_LINUX = "# Fankai-Placement"
    PLIST_LABEL = "com.fankai.placement"
    PLIST_PATH = Path("/Library/LaunchDaemons/com.fankai.placement.plist")
    DEFAULT_HOUR = 2
    DEFAULT_MINUTE = 0

    def __init__(self, config):
        self.config = config
        self.placement_executable = str(config.get_placement_executable_path())

    def is_task_scheduled(self):
        """Vérifie si une tâche planifiée pour Fankai-Placement existe déjà."""
        os_type = self.config.current_platform

        if os_type == 'Windows':
            result = subprocess.run(
                f'schtasks /Query /TN "{self.TASK_NAME_WINDOWS}"',
                shell=True, capture_output=True
            )
            return result.returncode == 0

        elif os_type == 'Linux':
            result = subprocess.run(
                "crontab -l | grep -F 'Fankai-Placement'",
                shell=True, capture_output=True
            )
            return result.returncode == 0

        elif os_type == 'Darwin':
            return self.PLIST_PATH.exists()

        return False

    def schedule_task(self):
        """Crée une tâche planifiée pour exécuter le placement tous les jours à 02:00."""
        os_type = self.config.current_platform
        logging.info(f"Création d'une tâche planifiée quotidienne pour {os_type}...")

        if os_type == 'Windows':
            self._schedule_windows()
        elif os_type == 'Linux':
            self._schedule_linux()
        elif os_type == 'Darwin':
            self._schedule_macos()

        logging.info("Tâche planifiée créée avec succès.")

    def _schedule_windows(self):
        """Crée une tâche planifiée Windows via PowerShell."""
        ps_script = f"""
        $Action = New-ScheduledTaskAction -Execute '{self.placement_executable}' -Argument 'auto'
        $Trigger = New-ScheduledTaskTrigger -Daily -At 02:00
        Register-ScheduledTask -Action $Action -Trigger $Trigger -TaskName '{self.TASK_NAME_WINDOWS}' -Description 'Run Fankai Placement daily at 2 AM' -Force
        """
        subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script], check=True)

    def _schedule_linux(self):
        """Ajoute une entrée cron pour Linux."""
        cron_job = f"{self.DEFAULT_MINUTE} {self.DEFAULT_HOUR} * * * {self.placement_executable} auto {self.TASK_COMMENT_LINUX}"
        subprocess.run(
            f'(crontab -l 2>/dev/null; echo "{cron_job}") | crontab -',
            shell=True, check=True
        )

    def _schedule_macos(self):
        """Crée un fichier plist launchd pour macOS."""
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{self.placement_executable}</string>
        <string>auto</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{self.DEFAULT_HOUR}</integer>
        <key>Minute</key>
        <integer>{self.DEFAULT_MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{self.config.log_path}/fankai_placement_launchd.log</string>
    <key>StandardErrorPath</key>
    <string>{self.config.log_path}/fankai_placement_launchd_error.log</string>
</dict>
</plist>
"""
        with open(self.PLIST_PATH, "w") as f:
            f.write(plist_content)
        subprocess.run(["sudo", "launchctl", "load", "-w", str(self.PLIST_PATH)], check=True)

    def remove_task(self):
        """Supprime la tâche planifiée existante."""
        os_type = self.config.current_platform
        logging.info(f"Suppression de la tâche planifiée pour {os_type}...")

        if os_type == 'Windows':
            subprocess.run(
                f'schtasks /Delete /TN "{self.TASK_NAME_WINDOWS}" /F',
                shell=True, check=True
            )
        elif os_type == 'Linux':
            subprocess.run(
                "crontab -l | grep -v 'Fankai-Placement' | crontab -",
                shell=True, check=True
            )
        elif os_type == 'Darwin':
            if self.PLIST_PATH.exists():
                subprocess.run(["sudo", "launchctl", "unload", str(self.PLIST_PATH)], check=True)
                self.PLIST_PATH.unlink()

        logging.info("Tâche planifiée supprimée.")


class UIManager:
    """Gère l'interaction avec l'utilisateur."""
    def __init__(self, db_manager, scheduler_manager):
        self.db_manager = db_manager
        self.scheduler_manager = scheduler_manager

    def display_prerequisites_status(self):
        """Affiche le statut des prérequis et retourne True si tous sont configurés."""
        missing = self.db_manager.verify_prerequisites()

        if missing:
            logging.error("Configuration incomplète. Les paramètres suivants doivent être configurés :")
            for key in missing:
                logging.error(f"  - {key}")
            logging.error("\nVeuillez d'abord lancer Fankai-Placement manuellement pour configurer ces paramètres.")
            return False

        logging.info("Tous les prérequis sont configurés correctement.")
        return True

    def display_scheduler_status(self):
        """Affiche le statut actuel de la planification."""
        if self.scheduler_manager.is_task_scheduled():
            logging.info("Une tâche de placement automatique quotidienne est ACTIVE (02h00).")
            return True
        else:
            logging.info("Aucune tâche de placement automatique n'est configurée.")
            return False


class Application:
    """Classe principale qui orchestre le script."""
    def __init__(self):
        self.config = Config()
        self.db_manager = DatabaseManager(self.config.db_path)
        self.scheduler_manager = SchedulerManager(self.config)
        self.ui_manager = UIManager(self.db_manager, self.scheduler_manager)

    def run(self):
        """Exécute le processus complet de configuration."""
        self.config.ensure_dirs_exist()
        os.chdir(self.config.fankai_app_path)
        setup_logging(self.config.log_path)

        print(pyfiglet.figlet_format("FANKAI-SCHED"))

        # Vérifier les prérequis
        if not self.ui_manager.display_prerequisites_status():
            return

        # Vérifier que l'exécutable de placement existe
        if not self.config.get_placement_executable_path().exists():
            logging.error("L'exécutable Fankai-Placement est introuvable.")
            logging.error("Veuillez d'abord lancer Fankai-All pour télécharger les outils.")
            return

        # Afficher le statut actuel et proposer des options
        is_scheduled = self.ui_manager.display_scheduler_status()

        if is_scheduled:
            choice = input("\nVoulez-vous (d)ésactiver la tâche ou (q)uitter ? [d/q] : ").lower()
            if choice == 'd':
                try:
                    self.scheduler_manager.remove_task()
                except Exception as e:
                    logging.error(f"Échec de la suppression : {e}")
        else:
            choice = input("\nVoulez-vous activer le placement automatique quotidien à 02h00 ? (o/n) : ").lower()
            if choice == 'o':
                try:
                    self.scheduler_manager.schedule_task()
                except Exception as e:
                    logging.error(f"Échec de la création de la tâche planifiée : {e}")
                    logging.error("Veuillez essayer de lancer ce script avec des privilèges administrateur/sudo.")

        logging.info("\nConfiguration terminée.")


# --- Point d'entrée ---

def handle_interrupt(sig, frame):
    print("\nSignal d'interruption reçu. Au revoir !")
    sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, handle_interrupt)
    try:
        app = Application()
        app.run()
    except Exception as e:
        logging.critical(f"Une erreur fatale est survenue: {e}", exc_info=True)

    input("\nAppuyez sur Entrée pour quitter.")
