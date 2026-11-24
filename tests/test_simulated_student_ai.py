import os
import time
import json
import requests
from google import genai
from google.genai import types

BASE_URL = "http://127.0.0.1:8000"
client = genai.Client(api_key="AQ.Ab8RN6LP_VAxrVm5rvWeM4seA9Biz5Yltvt-bWMVhjDTLKCGSg")

SYSTEM_INSTRUCTION_JANEK = (
    "Jesteś Jankiem, uczniem 8 klasy szkoły podstawowej. "
    "Twoja aktualna wiedza z matematyki obejmuje proste działania, ale masz blokadę i brak wiedzy "
    "Masz problem TYLKO z dodawaniem ułamków (dodajesz też mianowniki). Porównywanie i resztę ułamków rozumiesz normalnie jak ósmoklasista. "
    "Zasady zachowania:\n"
    "1. Odpowiadaj naturalnie, jak nastolatek, bez naukowego żargonu.\n"
    "2. NIE korzystaj ze swojej ogólnej wiedzy matematycznej modelu AI do rozwiązywania zadań z ułamków, "
    "dopóki w historii tej rozmowy nie zobaczysz konkretnego wyjaśnienia lub przykładu, który Ci to wytłumaczy.\n"
    "3. Jeśli nie rozumiesz zadania, przyznaj się do tego i spróbuj policzyć je intuicyjnie (popełniając swój typowy błąd).\n"
    "4. Bądź całkowicie szczery w ocenie swoich umiejętności – nie chwal niczego na siłę."
)

PRE_TEST_PROMPT = (
    "Rozwiąż poniższe zadanie i napisz krótko, jak do niego podszedłeś:\n"
    "Zadanie: Oblicz 3/4 + 1/4.\n"
    "Na końcu oceń w skali 1-5, na ile jesteś pewien swojego wyniku."
)


def build_task_prompt(statement: str, options_str: str, accumulated_feedback: str = "") -> str:
    context_block = ""
    if accumulated_feedback:
        context_block = (
            "Oto podsumowanie wyjaśnień, które zobaczyłeś w poprzednich zadaniach:\n"
            f"---\n{accumulated_feedback}\n---\n"
            "Spróbuj zastosować te wskazówki, jeśli pasują do obecnego zadania.\n\n"
        )

    return (
        f"{context_block}"
        f"Oto kolejne zadanie do rozwiązania:\n"
        f"Treść: {statement}\n\n"
        f"Dostępne opcje:\n{options_str}\n\n"
        f"Wybierz jedną opcję zgodnie ze swoim aktualnym stanem wiedzy. "
        f"Zwróć odpowiedź WYŁĄCZNIE jako czysty obiekt JSON z kluczem 'selected_option_id'."
    )


def build_post_test_prompt(accumulated_feedback: str) -> str:
    return (
        "Przerobiłeś przed chwilą serię zadań. Oto wszystkie wyjaśnienia, jakie zobaczyłeś w trakcie pracy:\n"
        f"---\n{accumulated_feedback if accumulated_feedback else 'Brak wyjaśnień - rozwiązywałeś zadania bez podpowiedzi.'}\n---\n\n"
        "Teraz rozwiąż ponownie zadanie z początku:\n"
        "Zadanie: Oblicz 10/3 + 9/3.\n\n"
        "Opisz krok po kroku swój tok myślenia, opierając się TYLKO na tym, czego dowiedziałeś się z powyższych wyjaśnień. "
        "Na końcu oceń w skali 1-5 swoją pewność siebie przy tym zadaniu i napisz jednym zdaniem, czy coś się rozjaśniło."
    )


def ask_gemini_as_student(prompt_text: str, force_json: bool = False) -> str:
    """Odpytuje Gemini w roli Janka z obsługą błędów 503."""
    max_retries = 5
    config_args = {
        "system_instruction": SYSTEM_INSTRUCTION_JANEK,
        "temperature": 0.3,
    }
    if force_json:
        config_args["response_mime_type"] = "application/json"

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-3.1-flash-lite',
                contents=prompt_text,
                config=types.GenerateContentConfig(**config_args),
            )
            return response.text
        except Exception as e:
            if "503" in str(e) and attempt < max_retries - 1:
                print(f"  ⚠️ Serwer Gemini przeciążony (503). Próba {attempt + 1}/{max_retries} za 4s...")
                time.sleep(4)
            else:
                return f"Błąd AI: {e}"


def test_run_multi_round_blind_experiment():
    # Świeży uczeń, ale jego ID jest stałe przez wszystkie rundy w tym teście
    student_id = f"janek-multi-round-{int(time.time())}"
    domain = "fractions"
    num_rounds = 7  # Liczba kolejnych sesji nauki (3 rundy x ~7 zadań = ~21 zadań)

    print(f"🔬 START WIELOSESYJNEJ ŚLEPEJ PRÓBY (Student ID: {student_id}, Rundy: {num_rounds})")

    # --- KROK 1: PRE-TEST ---
    print("\n📝 [KROK 1] Pre-test diagnostyczny (przed nauką)...")
    pre_result = ask_gemini_as_student(PRE_TEST_PROMPT)
    print(f"  📊 Janek przed nauką:\n{pre_result}\n")

    all_feedbacks = []

    # --- KROK 2: PĘTLA WIELU SESJI (ESKALACJA POZIOMU W PROFILERZE) ---
    for round_num in range(1, num_rounds + 1):
        print(f"\n=======================================================")
        print(f"🚀 SESJA NAUKI {round_num}/{num_rounds}")
        print(f"=======================================================")

        # Tworzenie planu (Profiler powinien uwzględnić wyniki z poprzednich rund!)
        res = requests.post(f"{BASE_URL}/api/v1/learning-plans", json={
            "selected_domain": domain,
            "student_id": student_id,
            "curriculum_context": {
                "grade": 8,
                "education_stage": "primary school",
                "curriculum_level": "STANDARD"
            }
        })
        assert res.status_code == 200, f"Błąd tworzenia planu (runda {round_num}): {res.text}"
        plan_id = res.json().get("plan_id")
        print(f"  ✅ Plan utworzony: {plan_id}")

        # Pobieranie zadań ze strumienia
        tasks_res = requests.get(
            f"{BASE_URL}/api/v1/learning-plans/{plan_id}/tasks",
            params={"student_id": student_id}
        )
        assert tasks_res.status_code == 200, f"Błąd pobierania zadań: {tasks_res.text}"

        tasks_list = []
        for line in tasks_res.text.splitlines():
            if line.strip():
                try:
                    event_obj = json.loads(line)
                    data = event_obj.get("data", event_obj)
                    if "presentation_id" in data and "options" in data:
                        tasks_list.append(data)
                except json.JSONDecodeError:
                    continue

        print(f"  📥 Pobrano {len(tasks_list)} zadań w sesji {round_num}.")

        # Rozwiązywanie zadań w danej sesji
        for idx, task in enumerate(tasks_list, 1):
            skill_info = task.get("skill_id", "brak-skill-id")
            diff_info = task.get("difficulty", "?")
            print(
                f"\n  --- [Sesja {round_num} | Zad. {idx}/{len(tasks_list)}] Skill: {skill_info} | Trudność: {diff_info} ---")
            print(f"  Treść: {task['statement']}")

            options_str = "\n".join([f"ID: {opt['id']} -> {opt['text']}" for opt in task['options']])
            # Przekazujemy dotychczasową wiedzę Janka (ostatnie 5 wyjaśnień, żeby nie zapchać kontekstu)
            recent_knowledge = "\n\n".join(all_feedbacks[-5:])
            prompt = build_task_prompt(task['statement'], options_str, recent_knowledge)

            ai_raw = ask_gemini_as_student(prompt, force_json=True)
            try:
                chosen_id = json.loads(ai_raw).get("selected_option_id", task['options'][0]['id'])
            except Exception:
                chosen_id = task['options'][0]['id']

            ans_res = requests.post(
                f"{BASE_URL}/api/v1/tasks/{task['presentation_id']}/answer",
                json={
                    "student_id": student_id,
                    "session_id": f"sesja-{round_num}-{plan_id}",
                    "selected_option_id": chosen_id,
                    "response_time_ms": 15000,
                    "attempts": 1,
                    "hints_used": 0
                }
            )

            if ans_res.status_code == 200:
                ans_data = ans_res.json()
                is_correct = ans_data.get("correct")
                explanation = ans_data.get("explanation", "")
                steps = "\n".join(ans_data.get("solution_steps", []))

                # Podgląd aktualnego ability z profilera
                skill_state = ans_data.get("skill_state", {})
                ability = skill_state.get("ability", "N/A")

                print(f"  Wynik: {'✅ POPRAWNA' if is_correct else '❌ BŁĘDNA'} | Nowe ability ({skill_info}): {ability}")

                current_feedback = f"Zadanie: {task['statement']}\nWyjaśnienie: {explanation}\nKroki:\n{steps}"
                all_feedbacks.append(current_feedback)
            else:
                print(f"  ⚠️ Błąd API ({ans_res.status_code}): {ans_res.text}")

    # --- KROK 3: POST-TEST PO WSZYSTKICH RUNDACH ---
    print("\n=======================================================")
    print("📝 [KROK 3] Post-test diagnostyczny (po 3 sesjach nauki)...")
    # Wybieramy unikalne wyjaśnienia z całej nauki
    accumulated_text = "\n\n".join(all_feedbacks[-10:])
    post_prompt = build_post_test_prompt(accumulated_text)

    post_result = ask_gemini_as_student(post_prompt)
    print(f"  📊 Janek po pełnym cyklu nauki:\n{post_result}\n")
    print("🏁 EKSPERYMENT WIELOSESYJNY ZAKOŃCZONY!")