import sys, os
sys.path.insert(0, os.path.abspath("src"))
from nadia_ai.utils.extraction import extract_inheritance_data, is_worth_llm_compute, clean_legal_text

# A representative BOE-N / TEJU notarial inheritance edict text
sample = """
EDICTO. Doña María Pérez García, Notario del Ilustre Colegio de Aragón, con
residencia en Zaragoza, hago saber: Que en mi notaría se tramita acta de
notoriedad de declaración de herederos abintestato del causante DON ANTONIO
MARTÍNEZ LÓPEZ, fallecido en Zaragoza el día 12 de marzo de 2026, en estado de
viudo, con vecindad civil aragonesa y domicilio en Calle Baltasar Gracián
número 7, Zaragoza. Se requiere a quienes se crean con derecho a la herencia.
Son llamados como herederos sus hijos DON JUAN MARTÍNEZ SANZ y DOÑA LUCÍA
MARTÍNEZ SANZ. Lo que se hace público para general conocimiento.
"""

print("=== is_worth_llm_compute:", is_worth_llm_compute(sample))
print("=== cleaned text preview ===")
print(clean_legal_text(sample)[:300])
print("\n=== extraction result ===")
data = extract_inheritance_data(sample, causante_hint="ANTONIO MARTÍNEZ LÓPEZ")
import json
print(json.dumps(data, indent=2, ensure_ascii=False))
