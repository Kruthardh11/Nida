import logging
from datetime import date
from tools.base import BaseTool, ToolResult
from core.fitness_manager import FitnessManager

logger = logging.getLogger("nida.fitness_tool")

class FitnessTool(BaseTool):
    """
    Nida Fitness Tracking Tool - interacts with FitnessManager to track calories,
    workouts, and rate the user's progress.
    """
    def __init__(self):
        super().__init__()
        self.manager = FitnessManager()

    @property
    def name(self) -> str:
        return "fitness"

    @property
    def description(self) -> str:
        return "Manage the user's fitness routine, log calories, protein, water, sleep, weight, or rate their day."

    @property
    def args_schema(self) -> dict:
        return {
            "action": "log | rate_day | summary | routine",
            "metric": "For log action: calories | protein | steps | water_liters | sleep_hours | weight_kg | workout_done | squats_done",
            "value": "Value to log (number or boolean)",
            "date": "Optional YYYY-MM-DD. Defaults to today."
        }

    def run(self, args: dict) -> ToolResult:
        action = args.get("action", "").lower()
        target_date = args.get("date", date.today().strftime("%Y-%m-%d"))

        if action == "log":
            metric = args.get("metric", "").lower()
            value = args.get("value")
            
            if not metric or value is None:
                return ToolResult(success=False, error="Metric and value are required to log.")
                
            self.manager.log_metric(target_date, metric, value)
            return ToolResult(success=True, output=f"Logged {value} for {metric} on {target_date}.")

        elif action == "rate_day":
            rating = self.manager.rate_day(target_date)
            output = f"Rating for {target_date}: {rating['score']}/10.\nFeedback: {rating['feedback']}"
            return ToolResult(success=True, output=output)

        elif action == "summary":
            summary = self.manager.get_weekly_summary()
            return ToolResult(success=True, output=summary["feedback"])
            
        elif action == "routine":
            routine = self.manager.get_todays_routine()
            return ToolResult(success=True, output=routine)

        return ToolResult(success=False, error=f"Unknown fitness action: {action}")
