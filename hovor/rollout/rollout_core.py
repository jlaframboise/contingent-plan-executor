from hovor.outcome_determiners.rasa_outcome_determiner import RasaOutcomeDeterminer
from hovor.rollout.semantic_similarity import get_action_confidences


class Rollout:
    def __init__(self, configuration_provider, rollout_cfg):
        self.configuration_provider = configuration_provider
        # convert conditions/effects to sets
        for act_cfg in rollout_cfg["actions"].values():
            act_cfg["condition"] = set(act_cfg["condition"])
            for out, out_vals in act_cfg["effect"].items():
                act_cfg["effect"][out] = set(out_vals)
        self.rollout_cfg = rollout_cfg
        self.current_state = set(rollout_cfg["initial_state"])
        self.applicable_actions = set()

    def get_highest_intents(self, action, utterance):
        data = self.configuration_provider._configuration_data
        rasa_outcome_determiner = RasaOutcomeDeterminer(
            data["actions"][action]["effect"]["outcomes"],
            data["context-variables"],
            data["intents"],
        )
        outcome_groups = self.configuration_provider._create_outcome_group(
            action, data["actions"][action]["effect"]
        )._outcome_groups
        chosen_intent, entities, ranked_groups = rasa_outcome_determiner.get_final_rankings(
            utterance["USER"], outcome_groups
        )
        return ranked_groups


    def get_applicable_actions(self):
        # get all applicable actions, disqualifying non-dialogue actions
        applicable_actions = {act for act in self.rollout_cfg["actions"] if self.rollout_cfg["actions"][act]["condition"].issubset(self.current_state) and self.configuration_provider._configuration_data["actions"][act]["type"]
            in ["dialogue", "message"]}
        if len(applicable_actions) == 0:
            raise NotImplementedError(
                "No applicable actions found past this point. Note that api and system actions are not currently handled."
            )
        return applicable_actions

    def update_state(self, new_fluents):
        for f in new_fluents:
            if "NegatedAtom" in f:
                raw_f = f.split("NegatedAtom ")[1][:-2]
                if f"Atom {raw_f}()" in self.current_state:
                    self.current_state.remove(f"Atom {raw_f}()")
                self.current_state.add(f)
            else:
                raw_f = f.split("Atom ")[1][:-2]
                if f"NegatedAtom {raw_f}()" in self.current_state:
                    self.current_state.remove(f"NegatedAtom {raw_f}()")
                self.current_state.add(f)

    def update_state_applicable_actions(self, 
        most_conf_act,
        outcome_group_name,
    ):
        return self.update_state(
            self.rollout_cfg["actions"][most_conf_act]["effect"][outcome_group_name],
        ), self.get_applicable_actions()

    def handle_hovor_utterance(self, 
        utterance,
        prev_action=None,
        prev_intent_out=None,
    ):
        data = self.configuration_provider._configuration_data
        if prev_action:
            # if a dialogue statement is possible, update the message variants according to the previous action
            if "dialogue_statement" in self.applicable_actions:
                if prev_intent_out["intent"] == "fallback":
                    if "fallback_message_variants" in data["actions"][prev_action]:
                        data["actions"][
                            "dialogue_statement"
                        ]["message_variants"] = data["actions"][prev_action][
                            "fallback_message_variants"
                        ]
                else:
                    data["actions"]["dialogue_statement"][
                        "message_variants"
                    ] = prev_intent_out["outcome"]["response_variants"]

        return get_action_confidences(
            data, utterance["HOVOR"], self.applicable_actions
        )


    def run_partial_conversation(self, conversation):
        data = self.configuration_provider._configuration_data

        for act in data["actions"]:
            data["actions"][act]["name"] = act

        applicable_actions = set()

        applicable_actions = self.get_applicable_actions(current_state, self.rollout_cfg["actions"])
        if len(applicable_actions) == 0:
            raise AssertionError("No valid initial state.")

        most_conf_intent_out = None
        most_conf_act = None
        while len(conversation) > 0:
            utterance = conversation.pop(0)
            if "HOVOR" in utterance:
                most_conf_act = self.handle_hovor_utterance(
                    utterance,
                    applicable_actions,
                    most_conf_act,
                    most_conf_intent_out,
                )
                if len(conversation) != 0:
                    most_conf_act = list(most_conf_act.keys())[0]
                    # doesn't need/take user input; state/applicable actions must be updated manually
                    act_type = data["actions"][most_conf_act]["type"]
                    if act_type != "dialogue":
                        if act_type == "message":
                            (
                                current_state,
                                applicable_actions,
                            ) = self.update_state_applicable_actions(
                                most_conf_act,
                                current_state,
                                self.configuration_provider._create_outcome_group(
                                    most_conf_act, data["actions"][most_conf_act]["effect"]
                                ).name,
                            )
                        else:
                            raise NotImplementedError(
                                f"Cannot handle the outcome group {act_type}"
                            )
            else:
                most_conf_intent_out = self.get_highest_intents(
                    self.configuration_provider, most_conf_act, utterance
                )
                if len(conversation) > 0:
                    most_conf_intent_out = most_conf_intent_out[0]
                    current_state, applicable_actions = self.update_state_applicable_actions(
                        most_conf_act,
                        most_conf_intent_out["outcome"].name,
                    )

        return most_conf_act if "HOVOR" in utterance else most_conf_intent_out
