"""Speaking instead of typing in the chat: a recording in, its text out."""
import pytest

from mangotree.brain import voice


class _Service:
    """Stands in for the transcription service: remembers what it was asked, answers a fixed text."""

    def __init__(self, text):
        self.text, self.asked = text, None
        self.audio = self
        self.transcriptions = self

    def create(self, **kw):
        self.asked = kw
        return type("R", (), {"text": self.text})()


def test_the_service_is_told_our_addresses_people_and_words():
    told = voice.vocabulary()
    assert "Varnum" in told and "Tahona" in told
    assert "Duwayne Tutt" in told and "hold-back" in told


def test_a_recording_becomes_text_and_is_sent_once_with_our_words():
    s = _Service("  What is open on\n Varnum Street?  ")
    out = voice.transcribe(b"x" * 4000, "speech.webm", "audio/webm;codecs=opus", client=s)
    assert out["text"] == "What is open on Varnum Street?"
    assert s.asked["model"] == voice.MODEL and s.asked["language"] == "en"
    assert s.asked["file"] == ("speech.webm", b"x" * 4000, "audio/webm")
    assert "Varnum" in s.asked["prompt"]


def test_a_press_with_nothing_said_is_refused_before_the_service_is_asked():
    s = _Service("anything")
    with pytest.raises(voice.VoiceError) as e:
        voice.transcribe(b"x" * 200, client=s)
    assert "Nothing was recorded" in str(e.value) and s.asked is None


def test_a_recording_that_is_too_long_is_refused():
    with pytest.raises(voice.VoiceError) as e:
        voice.transcribe(b"x" * (voice.MAX_BYTES + 1), client=_Service("x"))
    assert e.value.status == 413


def test_a_silent_recording_does_not_put_the_told_words_in_the_box():
    s = _Service("People: " + ", ".join(voice.PEOPLE) + ".")
    assert voice.transcribe(b"x" * 4000, client=s)["text"] == ""


def test_a_failure_of_the_service_is_a_plain_sentence():
    class Down(_Service):
        def create(self, **kw):
            raise RuntimeError("503 from upstream with internals")
    with pytest.raises(voice.VoiceError) as e:
        voice.transcribe(b"x" * 4000, client=Down(""))
    assert e.value.status == 502 and "internals" not in str(e.value)
