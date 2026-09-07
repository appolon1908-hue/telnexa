import time
from .db import SessionLocal
from .reconciliation import scan


def run_once():
    with SessionLocal.begin() as db:
        return scan(db)


def main():
    while True:
        run_once()
        time.sleep(30)


if __name__ == "__main__":
    main()
