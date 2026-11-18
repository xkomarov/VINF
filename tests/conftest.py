# добавляет formula1/ в sys.path, чтобы тесты импортировали extractor, wiki_regex и т. д.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
