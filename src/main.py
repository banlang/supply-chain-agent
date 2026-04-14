from dotenv import load_dotenv
load_dotenv()

from src.crew import SupplyChainCrew

if __name__ == "__main__":
    print("Starting Supply Chain Intelligence System...")
    print("=" * 50)
    result = SupplyChainCrew().kickoff()
    print("\n" + "=" * 50)
    print("FINAL REPORT:")
    print("=" * 50)
    print(result)
