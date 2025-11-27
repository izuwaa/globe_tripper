from dotenv import load_dotenv

load_dotenv()


# from pathlib import Path

# # from globe_tripper.agents.dispatcher_agent import DispatcherAgent
# from src.agents.dispatcher_agent import dispatcher_agent
# from globe_tripper.state.schema import AgentDecision, ConversationState, UserRequest
# from globe_tripper.utils.llm_client import LLMClient, LLMConfig


# def test_dispatcher_returns_agent_decision(tmp_path: Path):
#     prompt_path = tmp_path / "prompt.md"
#     prompt_path.write_text("You are a routing agent.", encoding="utf-8")

#     llm = LLMClient(LLMConfig(provider="test-provider", model="test-model"))
#     agent = DispatcherAgent(llm_client=llm, prompt_path=prompt_path)

#     state = ConversationState(history=["Hi there"])
#     request = UserRequest(user_id="user-123", query="Book me a trip to Lisbon")

#     decision = agent.run(state, request)

#     assert isinstance(decision, AgentDecision)
#     assert decision.agent == "dispatcher"
#     assert "Lisbon" in decision.next_action
