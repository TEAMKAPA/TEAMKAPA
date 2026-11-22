"""Detektory elementów formularza w OpenCV (#10, Etap C).

**Zasada:** detektory pracują wyłącznie na obrazie strony (``RenderedPage.image``). Nigdy nie
korzystają z warstwy wektorowej ani tekstowej PDF, żeby ten sam kod działał dla PDF, obrazów
cyfrowych i skanów, a ocena na etykietach (także przyszłych pre-etykietach z warstwy wektorowej)
nie była cykliczna (``docs/vision.md``).

Wszystkie progi są w ``DetectConfig`` (``backend/worker/vision/config.py``) w milimetrach strony
i są przeliczane na piksele według efektywnego DPI (``scale.Scale``).
"""
