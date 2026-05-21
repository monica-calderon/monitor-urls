from __future__ import annotations

from monitor import clean_error_detail, fetch_page, load_url_configs


def main() -> int:
    for config in load_url_configs():
        name = str(config["name"])
        print("=" * 80)
        print(name)
        print("=" * 80)

        try:
            result = fetch_page(config)
        except Exception as exc:
            print(f"ERROR: {clean_error_detail(exc)}")
            continue

        print(f"Metodo usado: {result.method}")
        print()
        print(result.text)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
