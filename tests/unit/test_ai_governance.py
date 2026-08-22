"""AI governance classification and the audit vocabulary.

Work package 7 of the Permanent Automated Regression Test Suite CR.

Classification decides whether an AI answer needs a recorded human decision
before anyone acts on it. Getting it wrong in one direction lets a
process-relevant suggestion through unreviewed; getting it wrong in the other
buries the reviewer in confirmations until they stop reading them. Both
failures are quiet.
"""

from __future__ import annotations

import pytest

import ai_governance as gov


# --- fixed call sites -------------------------------------------------------

@pytest.mark.parametrize(
    "call_site",
    ["recipe_optimization", "root_cause_assistant", "machine_settings_optimization"],
)
def test_a_function_that_proposes_a_change_is_process_relevant(call_site):
    classification, source = gov.classify(call_site)
    assert classification == gov.PROCESS_SAFETY_RELEVANT
    assert source == gov.SOURCE_FIXED_CALL_SITE
    assert gov.verification_required(classification)


@pytest.mark.parametrize("call_site", ["trend_analysis", "process_property_correlation"])
def test_a_function_that_only_describes_is_advisory(call_site):
    classification, source = gov.classify(call_site)
    assert classification == gov.TECHNICAL_ADVISORY
    assert source == gov.SOURCE_FIXED_CALL_SITE
    assert not gov.verification_required(classification)


def test_an_unknown_call_site_is_not_assumed_harmless():
    """A new AI surface arrives in the audit trail carrying technical content
    until somebody classifies it deliberately."""
    classification, source = gov.classify("some_new_screen_nobody_classified")
    assert classification == gov.TECHNICAL_ADVISORY
    assert classification != gov.INFORMATIONAL
    assert source == gov.SOURCE_FIXED_CALL_SITE


def test_a_fixed_call_site_is_never_downgraded_by_what_was_typed_into_it():
    """Content scanning can only raise, and only where it applies at all."""
    classification, _ = gov.classify("recipe_optimization", "what is the weather")
    assert classification == gov.PROCESS_SAFETY_RELEVANT


# --- content scanning on the free-form call sites ---------------------------

@pytest.mark.parametrize("call_site", list(gov.CONTENT_SCANNED_CALL_SITES))
def test_a_change_verb_paired_with_a_process_noun_escalates(call_site):
    classification, source = gov.classify(call_site, "Should we increase the TDI index?")
    assert classification == gov.PROCESS_SAFETY_RELEVANT
    assert source == gov.SOURCE_APPLICATION_RULE


def test_a_verb_on_its_own_does_not_escalate():
    classification, source = gov.classify("ask_assistant", "Can we change the report layout?")
    assert classification == gov.TECHNICAL_ADVISORY
    assert source == gov.SOURCE_FIXED_CALL_SITE


def test_a_process_noun_on_its_own_does_not_escalate():
    """Asking what a polyol costs is not a process change."""
    classification, _ = gov.classify("ask_assistant", "What does the polyol cost?")
    assert classification == gov.TECHNICAL_ADVISORY


@pytest.mark.parametrize(
    "question",
    [
        "where is the dataset behind the recipe report",   # "set" inside dataset
        "what is the offset used on the recipe chart",     # "set" inside offset
        "which preset is applied to this recipe chart",    # "set" inside preset
    ],
    ids=["dataset", "offset", "preset"],
)
def test_a_verb_term_buried_inside_another_word_does_not_escalate(question):
    """The defect this rule was written around.

    Plain substring matching read a verb term out of the middle of an ordinary
    noun, so a question that merely mentioned a dataset or a compression-set
    reading was escalated to Process / Safety Relevant. Terms now match at a
    word start.

    Over-escalation is not a safe failure. A reviewer buried in confirmations
    that did not need one stops reading them, and the one that mattered goes
    through with the rest.
    """
    classification, source = gov.classify("ask_assistant", question)
    assert classification == gov.TECHNICAL_ADVISORY
    assert source == gov.SOURCE_FIXED_CALL_SITE


@pytest.mark.parametrize(
    "question",
    [
        "can we set the recipe index to 105",
        "should we optimize the recipe for a lower density",
        "what if we lower the water content",
    ],
    ids=["set", "optimize", "what if"],
)
def test_the_same_terms_used_as_a_real_request_still_escalate(question):
    """The counterpart, so the word-start rule cannot have gone too far."""
    classification, source = gov.classify("ask_assistant", question)
    assert classification == gov.PROCESS_SAFETY_RELEVANT
    assert source == gov.SOURCE_APPLICATION_RULE


# --- "compression set": the property name, not an instruction ---------------
#
# Charlie's correction of 22 August 2026, after this was reported to him.
# "Compression set" is the name of a foam physical property; the "set" in it
# belongs to the property. His instruction was narrow: leave "set" in the
# general change-command logic, exclude it only where the token belongs to the
# recognised phrase, and prove both sides.
#
# These two blocks are that pair. Neither is useful without the other: the
# first alone could be satisfied by deleting "set" from the verb list, and the
# second alone by changing nothing.

@pytest.mark.parametrize(
    "question",
    [
        "show me the compression set trend for this recipe",
        "what is the compression set on this grade",
        "compare the compression set of these two grades",
    ],
    ids=["trend", "value", "comparison"],
)
def test_a_descriptive_question_about_compression_set_does_not_escalate(question):
    """The word "set" here is half a property name, not a change command.

    Over-escalation is not a safe failure. A reviewer buried in confirmations
    that did not need one stops reading them, and the one that mattered goes
    through with the rest.
    """
    classification, source = gov.classify("ask_assistant", question)
    assert classification == gov.TECHNICAL_ADVISORY
    assert source == gov.SOURCE_FIXED_CALL_SITE


@pytest.mark.parametrize(
    "question",
    [
        "set the compression target to 8%",
        "reduce the compression set",
        "can we set the recipe index to 105",
    ],
    ids=["set a target", "change the property", "set something else"],
)
def test_a_real_instruction_using_set_still_escalates(question):
    """"set" is untouched in the general change-command logic.

    The exclusion is phrase-specific. Outside the property name, and inside it
    when the sentence is actually asking for a change, the verb still counts.
    """
    classification, source = gov.classify("ask_assistant", question)
    assert classification == gov.PROCESS_SAFETY_RELEVANT
    assert source == gov.SOURCE_APPLICATION_RULE


def test_the_exclusion_is_a_phrase_list_not_a_change_to_the_verb_list():
    """The shape of the fix, asserted so it cannot drift into the easy version.

    Deleting "set" from _CHANGE_VERBS would pass the descriptive tests above
    and quietly stop escalating every real instruction that uses it.
    """
    assert "set" in gov._CHANGE_VERBS
    assert "compression set" in gov.PROPERTY_PHRASES


def test_blanking_a_phrase_leaves_the_rest_of_the_sentence_where_it_was():
    """Same length, so no two words are pushed together into a false match."""
    masked = gov._without_property_phrases("show the compression set trend")
    assert len(masked) == len("show the compression set trend")
    assert "compression" not in masked
    assert masked.startswith("show the ")
    assert masked.endswith(" trend")


def test_the_scan_is_case_insensitive():
    classification, _ = gov.classify("ask_assistant", "SHOULD WE LOWER THE CATALYST?")
    assert classification == gov.PROCESS_SAFETY_RELEVANT


def test_an_empty_question_leaves_the_call_site_default_alone():
    for text in (None, "", "   "):
        classification, source = gov.classify("ask_assistant", text)
        assert classification == gov.TECHNICAL_ADVISORY
        assert source == gov.SOURCE_FIXED_CALL_SITE


def test_only_process_relevant_output_needs_a_recorded_decision():
    assert gov.verification_required(gov.PROCESS_SAFETY_RELEVANT)
    assert not gov.verification_required(gov.TECHNICAL_ADVISORY)
    assert not gov.verification_required(gov.INFORMATIONAL)


# --- the audit vocabulary ---------------------------------------------------

def test_no_review_yet_and_a_reviewer_who_deferred_are_different_states():
    """Charlie's review, 20 August 2026. Calling both of them Pending hid the
    difference between an answer nobody has touched and one a person engaged
    with and put off."""
    assert gov.VERIFICATION_OUTSTANDING != gov.REVIEW_PENDING
    assert gov.VERIFICATION_OUTSTANDING in gov.ALL_REVIEW_STATES
    assert gov.REVIEW_PENDING in gov.ALL_REVIEW_STATES


def test_the_outstanding_state_can_never_be_written():
    """It is derived - the absence of any review row - not a decision anyone
    records. If it were writable, a reviewer could file "nobody looked at this"."""
    assert gov.VERIFICATION_OUTSTANDING not in gov.RECORDABLE_REVIEW_STATUSES
    for status in gov.RECORDABLE_REVIEW_STATUSES:
        assert status != gov.VERIFICATION_OUTSTANDING


def test_both_unresolved_states_are_named_as_unresolved():
    assert set(gov.UNRESOLVED_REVIEW_STATES) == {
        gov.VERIFICATION_OUTSTANDING,
        gov.REVIEW_PENDING,
    }
    for resolved in (gov.REVIEW_ACCEPTED, gov.REVIEW_MODIFIED, gov.REVIEW_REJECTED):
        assert resolved not in gov.UNRESOLVED_REVIEW_STATES


def test_every_review_state_has_something_to_display():
    for state in gov.ALL_REVIEW_STATES:
        assert gov.REVIEW_DISPLAY.get(state), state


def test_every_classification_source_is_one_the_code_can_produce():
    produced = {
        gov.classify("recipe_optimization")[1],
        gov.classify("ask_assistant", "should we increase the water level")[1],
    }
    assert produced <= set(gov.CLASSIFICATION_SOURCES)
    assert gov.SOURCE_REVIEWER_OVERRIDE in gov.CLASSIFICATION_SOURCES


# --- the prompt hash --------------------------------------------------------

def test_the_prompt_hash_is_stable_for_the_same_text():
    assert gov.prompt_hash("You are a foam expert.") == gov.prompt_hash(
        "You are a foam expert."
    )


def test_an_edited_prompt_gets_a_different_hash():
    """The whole point: a later edit is visible in the audit trail without
    relying on memory or on Git history alone."""
    assert gov.prompt_hash("You are a foam expert.") != gov.prompt_hash(
        "You are a foam expert!"
    )


def test_the_hash_is_short_enough_to_read_in_a_table():
    digest = gov.prompt_hash("anything")
    assert len(digest) == 16
    assert all(c in "0123456789abcdef" for c in digest)


def test_no_prompt_has_no_hash_rather_than_the_hash_of_nothing():
    """The hash of an empty string is a real, constant value. Returning it
    would make "no prompt recorded" look like a specific prompt."""
    assert gov.prompt_hash(None) is None
    assert gov.prompt_hash("") is None
