"""Démarre l'interface locale : `python -m dofus_opti.web`."""

import uvicorn


def main() -> None:
    uvicorn.run("dofus_opti.web.app:app", host="127.0.0.1", port=8410, log_level="warning")


if __name__ == "__main__":
    main()
