"""Condense the skipped-counterparty dump into a decision list."""
import re

PATH = (
    r"C:\Users\SHIVANSH AGRAWAL\.cursor\projects"
    r"\c-Users-SHIVANSH-AGRAWAL-Desktop-MangoTree\agent-tools"
    r"\acf540c9-b6f9-4a9c-ab22-6b51889adc5d.txt"
)


def main() -> None:
    txt = open(PATH, encoding="utf-8", errors="replace").read()
    blocks = re.split(r"\n### ", txt)
    rows = []
    for b in blocks[1:]:
        lines = b.split("\n")
        head = lines[0]
        m = re.search(r"^(\S+)\s+\((\d+) messages, (\d+) mention", head)
        if not m:
            continue
        party, n, nprop = m.group(1), int(m.group(2)), int(m.group(3))
        samples = [l.strip() for l in lines[1:] if l.strip().startswith("20")]
        rows.append((party, n, nprop, samples))

    rows.sort(key=lambda r: (-r[2], -r[1]))
    print("DISTINCT COUNTERPARTIES:", len(rows))
    print("WITH PROPERTY MENTIONS :", sum(1 for r in rows if r[2] > 0))
    print("TOTAL MESSAGES         :", sum(r[1] for r in rows))

    print("\n" + "=" * 96)
    print("GROUP A — skipped mail that NAMES one of our properties (almost certainly in scope)")
    print("=" * 96)
    for party, n, nprop, samples in rows:
        if nprop == 0:
            continue
        print(f"\n{party}  —  {n} msgs, {nprop} naming a property")
        for s in samples[:6]:
            print("    ", s[:110])

    print("\n" + "=" * 96)
    print("GROUP B — no property named (judgement call)")
    print("=" * 96)
    for party, n, nprop, samples in rows:
        if nprop:
            continue
        print(f"\n{party}  —  {n} msgs")
        for s in samples[:4]:
            print("    ", s[:110])


if __name__ == "__main__":
    main()
