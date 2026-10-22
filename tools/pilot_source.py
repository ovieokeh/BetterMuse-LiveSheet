#!/usr/bin/env python3
"""Package distributable source without uploads, credentials, or private fixtures."""
from pathlib import Path
import argparse
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(args.output,'w:gz') as tf:
        for name in ['livesheet','tools','tests','docs','deploy','Dockerfile','.dockerignore','compose.yaml','pyproject.toml','LICENSE','THIRD_PARTY_NOTICES.md','README.md']:
            path=ROOT/name
            if not path.exists():continue
            for f in sorted(path.rglob('*')) if path.is_dir() else [path]:
                rel=f.relative_to(ROOT)
                if not f.is_file() or '__pycache__' in rel.parts or 'data' in rel.parts or f.suffix in {'.pyc','.pdf','.png','.jpg','.webp'}:continue
                tf.add(f,arcname=str(rel),recursive=False)

if __name__=='__main__':main()
