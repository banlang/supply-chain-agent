import yaml
from pathlib import Path
from crewai import Agent, Task, Crew, Process

from src.tools import inventory_analysis_tool, risk_detection_tool, supplier_comparison_tool
from src.guardrails import (
    inventory_output_guardrail,
    risk_output_guardrail,
    reorder_output_guardrail,
    portfolio_output_guardrail,
)
from src.schemas import RiskDetectionOutput, ReorderReport, PortfolioInsight

# Resolve config dir relative to this file: src/crew.py -> src/ -> project root
_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(filename: str) -> dict:
    with open(_CONFIG_DIR / filename, encoding="utf-8") as f:
        return yaml.safe_load(f)


class SupplyChainCrew:
    def __init__(self, verbose: bool = True):
        self._verbose  = verbose
        agents_cfg = _load_yaml("agents.yaml")
        tasks_cfg  = _load_yaml("tasks.yaml")

        # --- Agents ---
        self.data_analyst = Agent(
            **agents_cfg["data_analyst"],
            tools=[inventory_analysis_tool],
            verbose=verbose,
            llm="gpt-4o-mini",
        )
        self.risk_analyst = Agent(
            **agents_cfg["risk_analyst"],
            tools=[risk_detection_tool],
            verbose=verbose,
            llm="gpt-4o-mini",
        )
        self.recommendation_agent = Agent(
            **agents_cfg["recommendation_agent"],
            tools=[supplier_comparison_tool],
            verbose=verbose,
            llm="gpt-4o-mini",
        )
        self.portfolio_synthesiser = Agent(
            **agents_cfg["portfolio_synthesiser"],
            tools=[],                    # no tools — pure LLM reasoning
            verbose=verbose,
            llm="gpt-4o-mini",
        )

        # --- Tasks ---
        self.inventory_analysis_task = Task(
            **tasks_cfg["inventory_analysis_task"],
            tools=[inventory_analysis_tool],
            agent=self.data_analyst,
            guardrail=inventory_output_guardrail,
        )
        self.risk_detection_task = Task(
            **tasks_cfg["risk_detection_task"],
            tools=[risk_detection_tool],
            agent=self.risk_analyst,
            context=[self.inventory_analysis_task],
            guardrail=risk_output_guardrail,
            output_pydantic=RiskDetectionOutput,
        )
        self.reorder_recommendation_task = Task(
            **tasks_cfg["reorder_recommendation_task"],
            tools=[supplier_comparison_tool],
            agent=self.recommendation_agent,
            context=[self.risk_detection_task],
            guardrail=reorder_output_guardrail,
            output_pydantic=ReorderReport,
        )
        self.portfolio_synthesis_task = Task(
            **tasks_cfg["portfolio_synthesis_task"],
            agent=self.portfolio_synthesiser,
            context=[self.reorder_recommendation_task],
            guardrail=portfolio_output_guardrail,
            output_pydantic=PortfolioInsight,
        )

    def kickoff(self):
        crew = Crew(
            agents=[
                self.data_analyst,
                self.risk_analyst,
                self.recommendation_agent,
                self.portfolio_synthesiser,
            ],
            tasks=[
                self.inventory_analysis_task,
                self.risk_detection_task,
                self.reorder_recommendation_task,
                self.portfolio_synthesis_task,
            ],
            process=Process.sequential,
            verbose=self._verbose,
        )
        return crew.kickoff()
