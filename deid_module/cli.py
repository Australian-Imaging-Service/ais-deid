import argparse
from .engine import DeidEngine

def main():
    parser = argparse.ArgumentParser(description="Standalone DICOM Deid Module")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--recipe", default="recipe.dicom")

    args = parser.parse_args()

    engine = DeidEngine(args.recipe)
    engine.process_directory(args.input, args.output)

if __name__ == "__main__":
    main()
