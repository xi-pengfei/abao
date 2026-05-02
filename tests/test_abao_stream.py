from core.abao import Abao


class FakeLLM:
    def stream_chat(self, messages):
        yield "半"
        yield "句"


class FakeMemory:
    def retrieve_relevant(self, user_text, speaker="user", limit=4):
        return []

    def core_facts(self, speaker="user", limit=8):
        return []

    def recent_diary(self, n=3):
        return []


class FakePromptBuilder:
    def build(self, **kwargs):
        return [{"role": "user", "content": kwargs["user_text"]}]


class FakeMonitor:
    def __init__(self):
        self.observed = 0
        self.reported = 0

    def observe_interaction(self, **kwargs):
        self.observed += 1

    def report_drift_events(self, *args, **kwargs):
        self.reported += 1


class FakePersonality:
    def __init__(self):
        self.applied = 0
        self.shocked = 0

    def snapshot(self):
        return {}

    def apply_signals(self, *args, **kwargs):
        self.applied += 1
        return []

    def apply_shock(self, *args, **kwargs):
        self.shocked += 1
        return []


def test_stream_abort_does_not_commit_state():
    abao = Abao.__new__(Abao)
    abao.primary_llm = FakeLLM()
    abao.memory = FakeMemory()
    abao.prompt_builder = FakePromptBuilder()
    abao.monitor = FakeMonitor()
    abao.personality = FakePersonality()
    abao.recent_turns = []

    stream = abao.converse_stream("为什么这件事让我非常震撼！")
    assert next(stream) == "半"
    stream.close()

    assert abao.monitor.observed == 0
    assert abao.monitor.reported == 0
    assert abao.personality.applied == 0
    assert abao.personality.shocked == 0
