from deid.config import DeidRecipe
from deid.dicom import DicomParser
import os
from pathlib import Path

from .transforms import hash_id

class DeidEngine:
    def __init__(self, recipe_path: str):
        self.recipe = DeidRecipe(recipe_path)

    def process_file(self, infile: str, outfile: str):
        parser = DicomParser(infile)
        parser.parse()

        # apply recipe rules
        parser.apply(self.recipe)

        parser.save(outfile)

    def process_directory(self, input_dir: str, output_dir: str):
        input_dir = Path(input_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for file in input_dir.rglob("*.dcm"):
            relative = file.relative_to(input_dir)
            out_file = output_dir / relative
            out_file.parent.mkdir(parents=True, exist_ok=True)

            self.process_file(str(file), str(out_file))
