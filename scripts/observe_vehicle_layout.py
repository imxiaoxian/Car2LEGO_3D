"""Record compact, auditable side-view landmarks for native architecture fitting."""
import argparse
from pathlib import Path

from car2lego3d.layout import observe_layout


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--views",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    print(observe_layout(args.views, args.output)["fractions"])


if __name__=="__main__":
    main()
