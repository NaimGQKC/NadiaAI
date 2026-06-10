import json
import logging
import sqlite3
import requests
from nadia_ai.config import OLLAMA_MODEL, OLLAMA_URL
from nadia_ai.db import get_connection

logger = logging.getLogger("nadia_ai.qa_judge")

JUDGE_PROMPT = """Eres un Auditor de Calidad para un sistema de generación de leads inmobiliarios.
Tu tarea es verificar si la extracción realizada por una IA es CORRECTA o si contiene ALUCINACIONES.

DATOS EXTRAÍDOS:
- Fallecido: {causante}
- Herederos: {heirs}
- Dirección del Inmueble: {direccion}
- Ref. Catastral: {rc}

REGLAS DE AUDITORÍA:
1. ALUCINACIÓN DE DIRECCIÓN: Si la dirección es "Calle Principal", "Calle 123", o similar, es una ALUCINACIÓN.
2. ERROR DE NOTARÍA: Si la dirección del inmueble coincide con una Notaría (ej: "Sita en la Notaría de..."), es un ERROR. El inmueble debe ser una propiedad privada.
3. CONSISTENCIA: ¿Los herederos parecen nombres reales? ¿La Ref. Catastral tiene 20 caracteres?

Responde en formato JSON:
{{
  "score": 0-100,
  "status": "PASS" | "FAIL",
  "reason": "Explicación breve del fallo o acierto."
}}

SOLO responde el JSON."""

def judge_lead(lead):
    """Use the LLM to judge a single lead's quality."""
    prompt = JUDGE_PROMPT.format(
        causante=lead['causante'],
        heirs=lead['heir_names_json'],
        direccion=lead['direccion'],
        rc=lead['referencia_catastral']
    )
    
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json"
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return json.loads(content)
    except Exception as e:
        logger.error(f"Judge failed for lead {lead['id']}: {e}")
        return {"score": 0, "status": "ERROR", "reason": str(e)}

def run_qa(sample_size=50):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    
    # Get high-tier leads that were processed by AI
    leads = conn.execute("""
        SELECT * FROM leads 
        WHERE ai_extraction_done = 1 
        ORDER BY tier ASC, last_updated_at DESC 
        LIMIT ?
    """, (sample_size,)).fetchall()
    
    report = []
    passed = 0
    
    print(f"Starting QA Audit on {len(leads)} leads...")
    
    for lead in leads:
        result = judge_lead(lead)
        report.append({
            "id": lead["id"],
            "causante": lead["causante"],
            "result": result
        })
        if result.get("status") == "PASS":
            passed += 1
        
        print(f"Lead {lead['id']}: {result.get('status')} (Score: {result.get('score')})")
    
    # Generate summary report
    summary = {
        "total_audited": len(leads),
        "passed": passed,
        "failed": len(leads) - passed,
        "pass_rate": f"{(passed/len(leads)*100):.1f}%" if leads else "0%",
        "details": report
    }
    
    with open("QA_REPORT.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\nQA Audit Complete. Pass Rate: {summary['pass_rate']}")
    conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_qa()
