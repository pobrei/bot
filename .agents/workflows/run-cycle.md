---
name: run-cycle
description: Run a single DRY_RUN evaluation cycle and output a summary artifact
---

# Run Cycle Workflow

When the user triggers this workflow (e.g. by requesting a run cycle or typing `/run-cycle`), follow these exact steps:

1. **Execute Single Iteration**:
   Run the `main.py` bot script in a local terminal using the Python virtual environment (`source .venv/bin/activate && DRY_RUN=True python main.py`). Since `main.py` contains an infinite loop, let it complete one full evaluation cycle (fetching data, evaluating strategy, executing order/dry-run) and then gracefully terminate the process using the `manage_task` tool (send a SIGINT or kill the task).

2. **Parse Outputs**:
   Extract the following metrics from the execution logs:
   - Current market price (ticker price from the execution logs).
   - Generated signal / action (e.g. BUY, SELL, HOLD).
   - *Note*: If the bot's standard output does not surface the exact EMA(12), EMA(26), and RSI(14) values directly, temporarily inject a `print` or `logger.info` statement into `src/strategy.py` or inspect the DataFrame to capture these specific readings for the user.

3. **Generate Walkthrough Artifact**:
   Create a polished Markdown Walkthrough Artifact summarizing the cycle. The artifact should include:
   - **Market Snapshot**: The current market price and time of execution.
   - **Indicator Readings**: Fast EMA (12), Slow EMA (26), and RSI (14).
   - **Action Taken**: The generated signal and the simulated execution status.
   - **Simulated Portfolio**: The estimated EUR value and notional trade values based on the €5 starting capital constraint.
