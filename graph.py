import uuid
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, StateGraph, END
from langgraph.constants import Send

from src.state.state import OverallState, CusineState
from src.tools.tools import google_search_scrape
from src.chains.chains import (
    intent_chain,
    chitchat_chain,
    cusine_chain,
    intro_chain,
    need_for_recipe_chain,
    need_for_other_help_chain,
    write_recipe_chain,
    detect_recipe_need_chain,
    chains_config,
)
from src.helpers.helpers import visualise_runnable

# memory = SqliteSaver.from_conn_string(":memory:")
memory = MemorySaver()
workflow = StateGraph(OverallState)

# Get no of cusine from config
no_of_cusine = chains_config["cusine"]["no_of_cusine"]


# node functions
def intent(state: OverallState):
    print("--- Intent ---")
    messages = state["messages"]
    question = messages[-1].content
    response = intent_chain.invoke({"question": question})
    result = eval(response.content)
    return {
        "question": question,
        "about_cusine": result["about_cusine"],
        "extra_requirements": result["extra_requirements"],
    }


def chitchat(state: OverallState):
    print("--- Chitchat ---")
    messages = state["messages"]
    question = messages[-1].content
    response = chitchat_chain.invoke({"question": question, "messages": messages[:-1]})
    return {
        "question": question,
        "messages": [AIMessage(content=response.content, name="chitchat")],
        "last_node": "chitchat",
    }


def cusine(state: OverallState):
    print("--- Cusine ---")
    messages = state["messages"]
    question = messages[-1].content
    extra_requirements = state["extra_requirements"]
    response = cusine_chain.invoke(
        {"question": question, "extra_requirements": extra_requirements}
    )
    return {
        "question": question,
        "cusine_choices": eval(response.content),
    }


def intro(state: CusineState):
    print("--- Intro ---")
    cusine = state["cusine"]
    response = intro_chain.invoke({"cusine": cusine})
    return {"cusine_intro": [{"cusine": cusine, "intro": response.content}]}


def need_for_recipe(state: OverallState):
    print("--- Need for Recipe ---")
    cusine_choices = state["cusine_choices"]
    cusine_intro = "\n\n".join(
        [
            data["cusine"] + "：" + data["intro"]
            for data in state["cusine_intro"][-no_of_cusine:]
        ]
    )
    response = need_for_recipe_chain.invoke({"cusine_choices": cusine_choices})
    message = cusine_intro + "\n\n" + response.content
    return {
        "messages": [AIMessage(content=message, name="need_for_recipe")],
        "last_node": "need_for_recipe",
    }


def deny_recipe_need(state: OverallState):
    "This node is a stepping stone for the map-reduce+HITL pipeline"
    print("--- Deny Recipe ---")
    pass


def need_for_other_help(state: OverallState):
    print("--- Need for Other Help ---")
    # generate response for asking other help needed
    response = need_for_other_help_chain.invoke({"input": ""})
    if len(state["cusine_recipe_link"]) == 0:
        # no recipe website found
        message = response.content
    else:
        # generate response for displaying recipe website
        cusine_content = "\n\n".join(
            {
                data["cusine"]
                + "\n"
                + "步驟：\n"
                + data["recipe"]
                + "\n\n食譜連結："
                + data["link"]
                for data in state["cusine_recipe_link"][-no_of_cusine:]
            }
        )
        message = cusine_content + "\n\n" + response.content
    return {
        "messages": [AIMessage(content=message, name="need_for_other_help")],
        "last_node": "need_for_other_help",
    }


def confirm_recipe_need(state: OverallState):
    pass


def search_recipe(state: CusineState):
    print("--- Search and Write Recipe ---")
    cusine = state["cusine"]
    search_result = google_search_scrape.invoke(cusine + " 食譜")
    NO_RECIPE_RESULT = "沒有找到食譜，請自由發揮"
    if search_result:
        recipe_from_search = (
            NO_RECIPE_RESULT
            if "cookies" in search_result["body_text"]
            else search_result["body_text"]
        )
        recipe_link = search_result["link"]
    else:
        recipe_from_search, recipe_link = (
            NO_RECIPE_RESULT,
            "https://www.bbc.co.uk/food/recipes",
        )
    recipe = write_recipe_chain.invoke({"cusine": cusine, "recipe": recipe_from_search})
    return {
        "cusine_recipe_link": [
            {
                "cusine": cusine,
                "recipe": recipe.content,
                "link": recipe_link,
            }
        ]
    }


# conditional edges logic
def entry_point_router(state: OverallState):
    print("--- Entry Point Router ---")
    if "last_node" not in state:
        return "intent"
    elif state["last_node"] == "need_for_recipe":
        return "detect_recipe_need"
    else:
        return "intent"


def detect_recipe_need(state: OverallState):
    print("--- Detect Recipe Need ---")
    if "messages" in state:
        last_message = state["messages"][-1].content
        # last_conversation = state["messages"][-3:-1]
    response = detect_recipe_need_chain.invoke(
        {
            "answer": last_message,
            # "last_message": last_conversation,
        }
    )
    return {"recipe_need": response.content}


def intent_router(state: OverallState):
    print("--- Intent Router ---")
    about_cusine = state["about_cusine"]
    if about_cusine:
        return "cusine"
    else:
        return "chitchat"


def recipe_need_router(state: OverallState):
    print("--- Recipe Need Router ---")
    recipe_need = state["recipe_need"]
    if recipe_need == "no":
        return "deny_recipe_need"
    elif recipe_need == "yes":
        return "confirm_recipe_need"
    elif recipe_need == "others":
        return "chitchat"


def map_to_intro(state: OverallState):
    print("--- Map to Intro ---")
    return [Send("intro", {"cusine": choice}) for choice in state["cusine_choices"]]


def map_to_search_recipe(state: OverallState):
    print("--- Map to Search Recipe ---")
    return [
        Send("search_recipe", {"cusine": data["cusine"], "intro": data["intro"]})
        for data in state["cusine_intro"]
    ]


# nodes
workflow.add_node("intent", intent)
workflow.add_node("chitchat", chitchat)
workflow.add_node("cusine", cusine)
workflow.add_node("intro", intro)
workflow.add_node("need_for_recipe", need_for_recipe)
workflow.add_node("detect_recipe_need", detect_recipe_need)
workflow.add_node("confirm_recipe_need", confirm_recipe_need)
workflow.add_node("deny_recipe_need", deny_recipe_need)
workflow.add_node("need_for_other_help", need_for_other_help)

workflow.add_node("search_recipe", search_recipe)


# edges
# workflow.add_edge(START, "intent")
workflow.set_conditional_entry_point(
    entry_point_router,
    {
        "intent": "intent",
        "detect_recipe_need": "detect_recipe_need",
    },
)
workflow.add_conditional_edges(
    "intent",
    intent_router,
    {"cusine": "cusine", "chitchat": "chitchat"},
)
workflow.add_conditional_edges(
    "detect_recipe_need",
    recipe_need_router,
    {
        "confirm_recipe_need": "confirm_recipe_need",
        "deny_recipe_need": "deny_recipe_need",
        "chitchat": "chitchat",
    },
)
workflow.add_edge("chitchat", END)
workflow.add_conditional_edges("cusine", map_to_intro, ["intro"])
workflow.add_edge("intro", "need_for_recipe")
workflow.add_edge("need_for_recipe", END)
workflow.add_edge("deny_recipe_need", "need_for_other_help")
workflow.add_conditional_edges(
    "confirm_recipe_need", map_to_search_recipe, ["search_recipe"]
)
workflow.add_edge("search_recipe", "need_for_other_help")
workflow.add_edge("need_for_other_help", END)


graph = workflow.compile(checkpointer=memory)
visualise_runnable(graph)


config = {"configurable": {"thread_id": str(uuid.uuid4())}}


# inference at terminal
def inference():

    while True:
        question = input("Enter your question: ")
        print("--------------------------------------")
        inputs = {"messages": [HumanMessage(content=question)]}
        for output in graph.stream(inputs, config):
            # stream() yields dictionaries with output keyed by node name
            for key, value in output.items():
                print(f"Output from node '{key}':")
                print("---")
                print(value)
            print("\n---\n")
        snapshot = graph.get_state(config)
        print("--- State Snapshot ---")
        for key, value in snapshot.values.items():
            if key != "messages":
                print(key + ": " + str(value))
            else:
                print("\n---------------- Start of Message History ----------------")
                for message in value:
                    message.pretty_print()
                print("----------------- End of Message History -----------------\n")


def inference_lite():
    # nodes for responses
    show_response_nodes = [
        "chitchat",
        "need_for_recipe",
        "need_for_other_help",
    ]
    while True:
        question = input("Enter your question: ")
        print("--------------------------------------")
        inputs = {"messages": [HumanMessage(content=question)]}
        for output in graph.stream(inputs, config):
            # stream() yields dictionaries with output keyed by node name
            for key, value in output.items():
                if key in show_response_nodes:
                    print("--------------------------------------")
                    print(value["messages"][-1].content)


if __name__ == "__main__":
    # inference()
    inference_lite()
