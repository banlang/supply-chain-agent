from dotenv import load_dotenv
load_dotenv()

from crewai import Crew, Process
from src.agents import data_analyst, risk_analyst, recommendation_agent
from src.tasks import inventory_analysis_task, risk_detection_task, reorder_recommendation_task

crew = Crew(
    agents=[data_analyst, risk_analyst, recommendation_agent],
    tasks=[inventory_analysis_task, risk_detection_task, reorder_recommendation_task],
    process=Process.sequential,
    verbose=True
)

if __name__ == "__main__":
    print("Starting Supply Chain Intelligence System...")
    print("=" * 50)
    result = crew.kickoff()
    print("\n" + "=" * 50)
    print("FINAL REPORT:")
    print("=" * 50)
    print(result)