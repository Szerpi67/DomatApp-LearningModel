#navigator = SkillNavigator(graph)
# requirements = navigator.get_requirements("MAT-FRA-003")


GRAPH = {
    "A": [],
    "B": ["A"],
    "C": ["B"],
    "D": ["C"]
}

class SkillNavigator:
    graph: dict[str, list]

    def __init__(self, graph: dict[str, list], skills: list[dict] | None = None):
        self.graph = graph.copy()
        self.skills = {skill["id"]: skill for skill in (skills or [])}
        if not self.valid_graph():
            raise ValueError("Invalid graph")

    @classmethod
    def from_skill_records(cls, skills: list[dict]):
        graph = {
            skill["id"]: skill.get("prerequisites", [])
            for skill in skills
        }
        return cls(graph, skills=skills)

    def has_skill(self, skill_id: str) -> bool:
        return skill_id in self.graph

    def get_prerequisites(self, skill_id: str) -> list[str]:
        return list(self.graph.get(skill_id, []))

    def get_skills(self) -> list[dict]:
        return list(self.skills.values())

    def valid_graph(self) -> bool:
        colors = {skill_id: 0 for skill_id in self.graph}

        def has_cycle(skill_id: str) -> bool:
            colors[skill_id] = 1
            requirements = self.graph.get(skill_id, [])
            for req_id in requirements:
                if req_id not in self.graph:
                    return True

                color = colors.get(req_id, 0)
                if color == 1:
                    raise ValueError("Cycle occured")
                if color == 0:
                    if has_cycle(req_id):
                        raise ValueError("Cycle occured")

            colors[skill_id] = 2
            return False

        for skill_id in self.graph:
            if colors[skill_id] == 0:
                if has_cycle(skill_id):
                    raise ValueError("Cycle occured: "+skill_id)

        return True

    def find_requirements(self, skill_id: str) -> list[str]:
        if skill_id not in self.graph:
            raise ValueError(f"Skill {skill_id} not found in graph")

        visited = set()
        result = []

        def dfs(curr_id: str):
            for req_id in self.graph.get(curr_id, []):
                if req_id not in self.graph:
                    raise ValueError(f"Missing requirement: {req_id}")

                if req_id not in visited:
                    visited.add(req_id)
                    dfs(req_id)


            if curr_id not in result and curr_id != skill_id:
                result.append(curr_id)

        dfs(skill_id)
        return result

