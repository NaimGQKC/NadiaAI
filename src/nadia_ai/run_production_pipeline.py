import subprocess
import sys
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("production_pipeline.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("production")

def run_command(cmd, name):
    logger.info(f"--- STARTING: {name} ---")
    start_time = time.time()
    try:
        # Run as a module and stream output
        process = subprocess.Popen(
            [sys.executable, "-m", f"nadia_ai.{cmd}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        for line in process.stdout:
            logger.info(line.strip())
            
        process.wait()
        elapsed = time.time() - start_time
        if process.returncode == 0:
            logger.info(f"--- FINISHED: {name} in {elapsed:.2f}s ---")
            return True
        else:
            logger.error(f"!!! FAILED: {name} with exit code {process.returncode} !!!")
            return False
    except Exception as e:
        logger.error(f"!!! EXCEPTION in {name}: {e} !!!")
        return False

def main():
    logger.info(f"Starting Production Night Pipeline at {datetime.now()}")
    
    # 1. Main Pipeline (Scrapers, Merge, Export)
    if not run_command("run", "Main Pipeline"):
        logger.error("Stopping due to Main Pipeline failure.")
        return

    # 2. Extraction Loop (Process all pending leads)
    if not run_command("run_all_extractions", "Extraction Loop"):
        logger.error("Stopping due to Extraction Loop failure.")
        return

    # 3. QA Judge
    if not run_command("qa_judge", "QA Quality Audit"):
        logger.error("QA Judge failed, but pipeline completed.")
    
    logger.info("Production Night Pipeline finished successfully.")

if __name__ == "__main__":
    main()
