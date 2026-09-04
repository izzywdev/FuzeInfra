import hashlib
import re

def build_marker_line(template_name: str, baseline_ref: str, raw_bytes: bytes) -> str:
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return f"# fuze:managed template={template_name} baseline={baseline_ref} digest=sha256:{digest}"

def parse_marker(text: str) -> dict | None:
    # Search for the fuze:managed marker line.
    match = re.search(r'#\s*fuze:managed(?:\s+\S+=\S+)*', text)
    if not match:
        return None
    line = match.group(0)
    # Find all key=value pairs on that line.
    pairs = re.findall(r'(\S+)=(\S+)', line)
    d = dict(pairs)
    if "template" in d and "digest" in d:
        digest = d["digest"]
        if digest.startswith("sha256:"):
            digest = digest[7:]
        return {
            "template": d["template"],
            "baseline": d.get("baseline", ""),
            "digest": digest
        }
    return None
