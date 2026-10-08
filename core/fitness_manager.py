import json
import logging
from pathlib import Path
from datetime import datetime, date, timedelta

logger = logging.getLogger("nida.fitness")

# The user's specific targets: 88kg body weight, 5'10-11".
# Maintenance calories: ~2500 kcal
# Goal: Recomposition (Lose fat, gain muscle).
# Creatine intake requires high hydration.
TARGETS = {
    "calories": 2200,      # Mild deficit for fat loss
    "protein": 180,        # ~2g per kg of bodyweight
    "steps": 12000,
    "water_liters": 4.0,   # High hydration for creatine
    "sleep_hours": 7.5
}

# Customized Bro Split (No dedicated leg day; Legs handled by 100 squats daily + walking)
WORKOUT_ROUTINE = {
    "Week 1": {
        "Monday": "Chest Day (Hypertrophy): Flat Bench Press 4x10, Incline Dumbbell Press 3x12, Pec Deck Fly 3x15, Pushups 3xMax.",
        "Tuesday": "Back Day (Hypertrophy): Lat Pulldowns 4x10, Barbell Rows 3x10, Seated Cable Rows 3x12, Face Pulls 3x15.",
        "Wednesday": "Shoulder Day (Hypertrophy): Overhead Press 4x10, Lateral Raises 4x15, Front Raises 3x12, Reverse Pec Deck 3x15.",
        "Thursday": "Arms Day (Biceps & Triceps): Barbell Curls 4x10, Tricep Pushdowns 4x12, Hammer Curls 3x12, Overhead Tricep Extension 3x12.",
        "Friday": "Forearms & Core: Wrist Curls 4x15, Reverse Curls 3x15, Planks 3x1min, Crunches 3x20.",
        "Saturday": "Cardio & Active Recovery: Light Jogging or Extra Walking.",
        "Sunday": "Rest Day: Complete Rest or Light Stretching."
    },
    "Week 2": {
        "Monday": "Chest Day (Strength): Flat Bench Press 5x5, Incline Barbell Press 4x8, Dips 3x8-10, Cable Crossovers 3x12.",
        "Tuesday": "Back Day (Strength): Deadlifts 4x5, Weighted Pull-ups 3x8, T-Bar Rows 4x8, Shrugs 4x12.",
        "Wednesday": "Shoulder Day (Strength): Arnold Press 4x8, Upright Rows 3x10, Heavy Lateral Raises 4x10, Cable Face Pulls 3x12.",
        "Thursday": "Arms Day (Heavy): Heavy Barbell Curls 4x8, Close-Grip Bench Press 4x8, Preacher Curls 3x10, Skull Crushers 3x10.",
        "Friday": "Forearms & Core (Heavy): Farmer's Walks 4x1min, Heavy Wrist Curls 3x12, Weighted Planks 3x45s, Hanging Leg Raises 3x15.",
        "Saturday": "Cardio & Active Recovery: Long Walk or Cycling.",
        "Sunday": "Rest Day: Complete Rest or Yoga."
    }
}

class FitnessManager:
    """Manages the daily fitness tracking logic, rating system, and routine assignment."""
    
    def __init__(self):
        self.file_path = Path("memory") / "fitness_log.json"
        self.file_path.parent.mkdir(exist_ok=True)
        self._ensure_file()

    def _ensure_file(self):
        if not self.file_path.exists():
            with open(self.file_path, "w") as f:
                json.dump({}, f)

    def _load_data(self) -> dict:
        try:
            with open(self.file_path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save_data(self, data: dict):
        with open(self.file_path, "w") as f:
            json.dump(data, f, indent=4)

    def get_todays_routine(self) -> str:
        """Determines if we are in Week 1 or Week 2 of the month, and gets today's routine."""
        today = date.today()
        day_name = today.strftime("%A")
        
        # Simple toggle: odd weeks of the year = Week 1, even weeks = Week 2
        week_num = today.isocalendar()[1]
        week_key = "Week 1" if week_num % 2 != 0 else "Week 2"
        
        routine = WORKOUT_ROUTINE[week_key].get(day_name, "Rest Day")
        return f"({week_key}) {day_name}'s Routine: {routine}\n\nDon't forget your daily 100 squats and 12k steps!"

    def log_metric(self, date_str: str, metric: str, value: float | str | int):
        """Logs a specific metric incrementally for a given date (YYYY-MM-DD)."""
        data = self._load_data()
        
        if date_str not in data:
            data[date_str] = {
                "calories": 0,
                "protein": 0,
                "steps": 0,
                "water_liters": 0.0,
                "sleep_hours": 0.0,
                "weight_kg": 0.0,
                "workout_done": False,
                "squats_done": False,
                "notes": []
            }
            
        today_log = data[date_str]
        
        # Additive metrics
        if metric in ["calories", "protein", "steps", "water_liters"]:
            today_log[metric] = today_log.get(metric, 0) + float(value)
        # Override metrics
        elif metric in ["sleep_hours", "weight_kg"]:
            today_log[metric] = float(value)
        # Boolean metrics
        elif metric in ["workout_done", "squats_done"]:
            today_log[metric] = str(value).lower() == "true"
        # List appending
        elif metric == "notes":
            today_log["notes"].append(str(value))
            
        self._save_data(data)
        return today_log

    def rate_day(self, date_str: str) -> dict:
        """Rates a specific day out of 10 based on the fitness goals."""
        data = self._load_data()
        log = data.get(date_str)
        
        if not log:
            return {"score": 0, "feedback": f"No data logged for {date_str}."}

        score = 0
        feedback = []

        # 1. Calories (Target: 2200. Max 2 points)
        cals = log.get("calories", 0)
        if cals == 0:
            feedback.append("You haven't logged any calories.")
        elif 1800 <= cals <= 2400:
            score += 2
            feedback.append("Calories are perfectly in the recomposition zone.")
        elif cals > 2400:
            score += 1
            feedback.append(f"Calories slightly high ({cals}). Watch your intake to stay in a deficit.")
        else:
            score += 1
            feedback.append(f"Calories too low ({cals}). Make sure you are eating enough to build muscle.")

        # 2. Protein (Target: 180g. Max 2 points)
        protein = log.get("protein", 0)
        if protein >= TARGETS["protein"]:
            score += 2
            feedback.append("Hit your protein goal! Excellent for muscle growth.")
        elif protein >= 140:
            score += 1
            feedback.append(f"Protein is decent ({protein}g), but aim a little higher (180g) for optimal gains.")
        else:
            feedback.append(f"Protein is low ({protein}g). Try to eat more lean meats, eggs, or whey.")

        # 3. Steps (Target: 12k. Max 2 points)
        steps = log.get("steps", 0)
        if steps >= TARGETS["steps"]:
            score += 2
            feedback.append("Crushed your 12k steps goal!")
        elif steps >= 8000:
            score += 1
            feedback.append(f"Good step count ({steps}), but keep pushing for that 12k target.")
        else:
            feedback.append(f"Step count is very low ({steps}). Remember, this is your primary leg workout and cardio!")

        # 4. Workouts & Squats (Max 2 points)
        if log.get("squats_done") and log.get("workout_done"):
            score += 2
            feedback.append("Killed the workout and got your 100 squats in. Great discipline!")
        elif log.get("workout_done"):
            score += 1
            feedback.append("You did your workout, but missed your 100 daily squats. Legs need love too!")
        elif log.get("squats_done"):
            score += 1
            feedback.append("Got your squats in, but missed the main workout routine.")
        else:
            feedback.append("No workout or squats logged today. Make sure it's an active rest day or get to the gym!")

        # 5. Hydration & Sleep (Max 2 points)
        water = log.get("water_liters", 0)
        sleep = log.get("sleep_hours", 0)
        recov_score = 0
        if water >= TARGETS["water_liters"]:
            recov_score += 1
            feedback.append("Excellent hydration, perfect for creatine absorption.")
        elif water > 0:
            feedback.append(f"Drink more water! You only logged {water} liters, aim for 4 liters with creatine.")
            
        if sleep >= TARGETS["sleep_hours"]:
            recov_score += 1
            feedback.append("Great sleep duration. Muscle grows while you rest.")
        elif sleep > 0:
            feedback.append(f"Try to get more sleep. {sleep} hours isn't optimal for recovery.")
            
        score += recov_score
        
        return {
            "score": score,
            "feedback": " ".join(feedback),
            "log": log
        }

    def get_weekly_summary(self) -> dict:
        """Aggregates data over the last 7 days."""
        data = self._load_data()
        today = date.today()
        
        total_cals, total_protein, total_steps = 0, 0, 0
        days_logged = 0
        workouts_completed = 0
        weights = []
        
        for i in range(7):
            d_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            if d_str in data:
                days_logged += 1
                total_cals += data[d_str].get("calories", 0)
                total_protein += data[d_str].get("protein", 0)
                total_steps += data[d_str].get("steps", 0)
                if data[d_str].get("workout_done"):
                    workouts_completed += 1
                if data[d_str].get("weight_kg", 0) > 0:
                    weights.append(data[d_str]["weight_kg"])
                    
        if days_logged == 0:
            return {"feedback": "You haven't logged any data in the past 7 days."}
            
        avg_cals = total_cals / days_logged
        avg_protein = total_protein / days_logged
        avg_steps = total_steps / 7  # Average over the whole week, not just logged days
        avg_weight = sum(weights) / len(weights) if weights else 0.0
        
        summary = (
            f"Over the last 7 days, you've logged data for {days_logged} days. "
            f"You averaged {avg_cals:.0f} calories and {avg_protein:.0f}g of protein per day. "
            f"You completed {workouts_completed} workouts and averaged {avg_steps:.0f} steps a day. "
        )
        if avg_weight > 0:
            summary += f"Your average weight was {avg_weight:.1f}kg. "
            
        if avg_cals > 2400 and avg_weight > 0:
            summary += "You are eating a bit high on calories for fat loss, try cutting back by 100-200 calories this coming week."
        elif avg_protein < 160:
            summary += "Your protein is low for optimal muscle gain. Focus heavily on protein intake this week!"
        else:
            summary += "You are absolutely crushing your recomposition goals. Keep this momentum!"
            
        return {"feedback": summary}
