# NadiaAI Context Dump: Inheritance Intelligence Engine

## 1. System Overview
NadiaAI is an inheritance lead generation engine. It scrapes public edicts (BOE, BOA, BOP), obituaries, and administrative notifications to identify high-intent real estate leads in Spain (primarily Zaragoza).

## 2. Data Schema (leads table)
- `causante`: The deceased person.
- `heir_name`: The primary heir or petitioner.
- `direccion`: Extracted property address (Target asset).
- `tier`: Triage (A: Full data, B: Name/Date only, C: Partial).
- `days_since_death`: Calculated from `date_of_death`.
- `ai_extraction_done`: Flag for LLM processing.
- `source_urls`: JSON list of raw document links.

## 3. Current Extraction Logic (Ollama/Mistral)
We use a two-step process:
1. **Raw Text Cleaning**: Strip Notary headers/footers to reduce noise.
2. **LLM Prompt**:
```python
EXTRACTION_PROMPT = """Eres un experto en derecho de sucesiones en España.
Analiza el texto y genera un objeto JSON...
{{
  "deceased_name": "Nombre del fallecido/causante.",
  "date_of_death": "Fecha de fallecimiento (YYYY-MM-DD).",
  "list_of_heirs": ["Nombres COMPLETOS de los herederos."],
  "property_address": "Dirección del inmueble o finca. IGNORA la notaría.",
  "referencia_catastral": "Ref. Catastral si aparece"
}}
REGLAS CRÍTICAS:
1. PROHIBIDO: No uses la dirección de la Notaría como 'property_address'.
2. Usa 'causante_hint' como referencia principal.
"""
```

## 4. Known Issues
- **Hallucinations**: LLM outputs generic addresses like "Calle Principal 123" when the text only mentions a Notary.
- **Notary Noise**: The Notary's office address is often the only address in the text, leading the LLM to misidentify it as the asset.
- **Encoding**: Occasional character garble from raw PDF extraction (`pdfplumber`).

## 5. Sample Raw Text (Snippet)
"...procedimiento tramitación del acta de notoriedad para la declaración de herederos abintestato de Don Félix Mederos Déniz, fallecido en Telde el 22 de marzo de 2026... promovido por Doña María Mederos... ante la Notaría de Telde sita en Calle Mayor 1..."

## 6. Proposed Validation Levers
- **Geocoding**: Check if the address exists via Google Maps/OpenStreetMap.
- **Catastro Hook**: Validate against the Spanish Cadastre (Sede Electrónica del Catastro) using the extracted Ref Catastral.
- **Cross-Source Search**: Use the Name + City to find the actual property in other administrative databases.
- **Regex Guardrails**: Hard-block "Calle Principal", "Calle 123", etc.
