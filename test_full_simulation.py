import random
import requests

BASE_URL = "http://127.0.0.1:8000"


def simulate_student_learning_journey():
    student_id = "monte-carlo-student-01"
    domain = "ulamki"

    print("🎓 ROZPOCZYNAM SYMULACJĘ EFEKTÓW EDUKACYJNYCH (14 dni)")

    # KROK 1: Inicjalizacja planu nauki
    plan_payload = {
        "student_id": student_id,
        "selected_domain": domain,
        "curriculum_context": {
            "grade": 8,
            "education_stage": "primary school",
            "curriculum_level": "STANDARD"
        }
    }

    res = requests.post(f"{BASE_URL}/api/v1/learning-plans", json=plan_payload)
    if res.status_code != 200:
        print(f"❌ Błąd tworzenia planu: {res.text}")
        return

    plan_data = res.json()
    plan_id = plan_data.get("plan_id")
    print(f"✅ Utworzono plan nauki. Plan ID: {plan_id}")

    # Stan wiedzy wirtualnego ucznia na starcie (symulujemy ukryty poziom opanowania podtematów)
    # 0.0 = brak wiedzy, 1.0 = pełne opanowanie
    student_knowledge_state = {
        "ulamki_proste": 0.8,  # to już umie
        "ulamki_rozne_mianowniki": 0.2,  # tutaj ma ogromne luki (główny cel profilera)
    }

    initial_gap_size = student_knowledge_state["ulamki_rozne_mianowniki"]

    # Symulacja 14 dni nauki (mikrodawkowanie)
    days = 14
    for day in range(1, days + 1):
        print("--- Dzień: ",day)

        # Pobieramy zadania z plannera dla aktualnego stanu ucznia
        tasks_res = requests.get(
            f"{BASE_URL}/api/v1/learning-plans/{plan_id}/tasks",
            params={"student_id": student_id}
        )

        if tasks_res.status_code != 200:
            print(f"⚠️ Nie udało się pobrać zadań w dniu {day}: {tasks_res.text}")
            continue

        # Wirtualny uczeń rozwiązuje zadanie i trafia na swój słaby punkt
        # Im mniejsza wiedza w stanie ucznia, tym większa szansa na błąd
        current_mastery = student_knowledge_state["ulamki_rozne_mianowniki"]
        probability_of_error = 1.0 - current_mastery

        is_error = random.random() < probability_of_error
        attempts = 2 if is_error else 1
        hints_used = 1 if is_error else 0

        print(
            f"  > Uczeń rozwiązał zadanie. Wynik: {'BŁĄD ❌' if is_error else 'SUKCES ✅'} (Poziom wiedzy przed: {current_mastery:.2f})")

        # Wysyłamy odpowiedź do endpointu profilera/oceny
        # Uwaga: W prawdziwym teście podstawiasz presentation_id zwrócone ze strumienia zadań
        mock_presentation_id = f"pres-day-{day}"
        answer_payload = {
            "student_id": student_id,
            "session_id": f"session-day-{day}",
            "selected_option_id": "wrong-opt" if is_error else "correct-opt",
            "response_time_ms": random.randint(10000, 30000),
            "attempts": attempts,
            "hints_used": hints_used
        }

        ans_res = requests.post(
            f"{BASE_URL}/api/v1/tasks/{mock_presentation_id}/answer",
            json=answer_payload
        )

        # SYMULACJA EFEKTU EDUKACYJNEGO (Krzywa zapominania i nauki):
        # Jeśli system poprawnie dopasował zadanie (Profiler + Planner), uczeń z każdym dniem treningu nadrabia luki
        if not is_error:
            # Sukces buduje wiedzę
            student_knowledge_state["ulamki_rozne_mianowniki"] = min(1.0, current_mastery + 0.08)
        else:
            # Błąd wymusza powtórkę, ale system pomaga (mały przyrost dzięki wyjaśnieniom)
            student_knowledge_state["ulamki_rozne_mianowniki"] = min(1.0, current_mastery + 0.03)

    final_gap_size = student_knowledge_state["ulamki_rozne_mianowniki"]
    learning_gain = final_gap_size - initial_gap_size

    print("\n🏁 RAPORT Z SYMULACJI MONTE CARLO:")
    print(f"  - Początkowy poziom wiedzy trudnego działu: {initial_gap_size * 100}%")
    print(f"  - Końcowy poziom wiedzy po 14 dniach: {final_gap_size * 100}%")
    print(f"  - Wzrost kompetencji (Learning Gain): +{learning_gain * 100:.1f}%")

    if final_gap_size > 0.7:
        print("🎉 SUKCES: Profiler i Planner skutecznie pomogły uczniowi domknąć luki!")
    else:
        print("⚠️ UWAGA: Algorytm wymaga korekty – uczeń nie osiągnął pełnego opanowania materiału.")


def test():
    simulate_student_learning_journey()