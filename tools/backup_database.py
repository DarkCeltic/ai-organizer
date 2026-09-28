"""Create a consistent backup of the AI Organizer SQLite database."""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from python_organizer_local_llm.config_defaults import config_base_dir, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=None,
                        help='Optional legacy/local YAML override.')
    parser.add_argument('--database', type=Path, default=None,
                        help='Explicit SQLite path (useful for AppAPI persistent storage).')
    args = parser.parse_args()

    if args.database:
        path = args.database.expanduser()
    else:
        config_name = str(args.config.expanduser()) if args.config else None
        config = load_config(config_name)
        path = Path(config.get('database', {}).get('path', 'data/ai_organizer.db')).expanduser()
        if not path.is_absolute():
            path = config_base_dir(config_name) / path

    if not path.is_file():
        raise FileNotFoundError(f'Refusing to create an empty database. No file at: {path}')
    destination = path.with_name(path.name + '.' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.backup')
    with sqlite3.connect(str(path)) as source:
        with sqlite3.connect(str(destination)) as target:
            source.backup(target)
            result = target.execute('PRAGMA integrity_check').fetchone()[0]
            if result != 'ok':
                raise RuntimeError(f'Backup failed integrity check: {result}')
    print(f'Original database: {path.resolve()}')
    print(f'Backup database:   {destination.resolve()}')
    print('Backup integrity: ok')


if __name__ == '__main__':
    main()
