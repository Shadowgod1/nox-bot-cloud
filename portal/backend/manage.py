"""Portal admin CLI entry point: python manage.py create-staff --email ops@example.kz (see nox_portal/manage.py)."""

import sys

from nox_portal.manage import main

if __name__ == "__main__":
    sys.exit(main())
