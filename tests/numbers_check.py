#!/usr/bin/env python3
"""Проверка разбора чисел в разделе «ЧИСЛА».

Проверка сверяет цифры оригинала с цифрами перевода, и два класса из неё
вычтены: подмена буквы цифрой в распознанном тексте и число, слипшееся
дефисом со словом. Оба вычитания опасны в одну сторону — вычтем лишнее, и
настоящая пропажа цифры пройдёт молча, а это ошибка, которую в переводе
никто не заметит.

    python3 tests/numbers_check.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from booktrans import build as B                        # noqa: E402

# (текст, число, вычитается ли).
COMPOUND = [
    ("Отчёт вышел 88-страничный, и его прочли все.", "88", True),
    ("Это были 2-D клетки кожи, не более того.", "2", True),
    ("Выступал 28-летний оратор, и он волновался.", "28", True),
    ("an eighty-eight-page report", "88", False),      # цифры тут нет вовсе
    # Цифра позади дефиса — обозначение, ей полагается уцелеть.
    ("Пандемия COVID-19 началась зимой.", "19", False),
    ("Он слушал MP-3 всю дорогу домой.", "3", False),
    # Диапазон и дата: за дефисом снова цифра, а не буква.
    ("Смотрите страницы 13-20 приложения.", "13", False),
    ("Годы 2019-2020 выпали из отчёта.", "2019", False),
    ("В 1988 году он умер, оставив запись.", "1988", False),
    ("Их было 15 человек, не считая детей.", "15", False),
]

# (текст оригинала, число, вычитается ли как порча распознавания).
OCR = [
    ("Kutira and 1, along with her husband", "1", True),      # 1 вместо I
    ("during World War 11 for the Office", "11", True),       # 11 вместо II
    ("sent to the 5t. Paul Academy", "5", True),              # приклеена к букве
    # Мера нарочно широкая: у распознанной книги любое одно- и двузначное
    # число вычитается, потому что именно такими цифрами распознавание
    # подменяет буквы. Настоящий номер тома в два знака вычтется вместе с
    # ними — цена за то, чтобы раздел не краснел от сотни ложных сообщений.
    ("Rev, 5ci. Irzsfrum. 73:34-37", "73", True),
    ("One day in 1958 John entered", "1958", False),
    ("P. 238-247 66. Lilly, John C.", "238", False),
]


# (текст оригинала, число, вычитается ли как «язык пишет это словом»).
SPELLED = [
    ("In part 1 of this book, I will draw on my own experience.", "1", True),
    ("As discussed in chapter 12, the brain adapts.", "12", True),
    ("Back in the 1980s and ’90s, a group of neuroscientists", "1980", True),
    ("Back in the 1980s and ’90s, a group of neuroscientists", "90", True),
    # Настоящие сведения рядом с теми же словами вычитаться не должны.
    ("Part of the group, some 12 people, stayed home.", "12", False),
    ("The chapter cites 40 studies on sleep.", "40", False),
    ("In 1980 the study began, and it ran for years.", "1980", False),
    ("They walked 90 kilometres that week.", "90", False),
]


# (оригинал, перевод, число, откуда смотрим, вычитается ли как пересчёт мер).
# Пересчёт в СИ задан промптом перевода, и проверке он виден дважды: число
# оригинала пропало, число перевода появилось. Обе половины ложные, но
# вычитать их можно только вместе: имперская мера в оригинале и метрическая
# в переводе. Одной половины мало — иначе прикрытой окажется настоящая потеря.
MEASURE = [
    ("showed his 165 pounds of body weight", "он весил 75 кг", "165", False, True),
    ("showed his 165 pounds of body weight", "он весил 75 кг", "75", True, True),
    ("a waist over forty inches", "талия больше 102 см", "102", True, True),
    ("the temperature hit 98 degrees Fahrenheit", "температура дошла до 37 °C",
     "98", False, True),
    # Пересчёта нет: в переводе те же меры, и число обязано уцелеть.
    ("showed his 165 pounds of body weight", "он весил 165 фунтов", "165", False, False),
    # Мера в переводе есть, а в оригинале имперской нет: число взялось ниоткуда.
    ("the sample held 40 mg/dl", "в пробе было 12 мг/дл", "12", True, False),
    # Число не при мере: пересчёт рядом не оправдывает его пропажу.
    ("in 1997 he weighed 165 pounds", "тогда он весил 75 кг", "1997", False, False),
]


# (перевод, число оригинала, вычитается ли как восстановленная степень).
POWER = [
    ("в биосфере около 10<sup>31</sup> фагов", "1031", True),
    ("и 10<sup>23</sup> заражений в секунду", "1023", True),
    # Степень в переводе есть, а это число не из неё: потеря настоящая.
    ("в биосфере около 10<sup>31</sup> фагов, открытых в 1896 году", "1917", False),
    # Разметки нет — значит и восстанавливать было нечего.
    ("в биосфере около 1031 фагов", "1031", False),
]


def main():
    bad = 0
    print("  число, слипшееся со словом:")
    for text, n, want in COMPOUND:
        got = B._compound(text, n)
        ok = got == want
        print(f"    {'вычтено ' if got else 'засчитано'}  {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"  [{n}] {text[:52]}")
        bad += not ok
    print("  порча распознавания:")
    for text, n, want in OCR:
        got = B._ocr_digit(text, n)
        ok = got == want
        print(f"    {'вычтено ' if got else 'засчитано'}  {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"  [{n}] {text[:52]}")
        bad += not ok
    print("  число, которое язык пишет словом:")
    for text, num, want in SPELLED:
        got = B._spelled(text, num)
        ok = got == want
        print(f"    {'вычтено ' if got else 'засчитано'}  {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"  [{num}] {text[:52]}")
        bad += not ok
    print("  пересчёт меры в систему СИ:")
    for s, t, num, back, want in MEASURE:
        got = B._measure(s, t, num, back=back)
        ok = got == want
        print(f"    {'вычтено ' if got else 'засчитано'}  {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"  [{num}] {s[:34]} → {t[:26]}")
        bad += not ok
    print("  степень, восстановленная переводом:")
    for t, num, want in POWER:
        got = B._power(t, num)
        ok = got == want
        print(f"    {'вычтено ' if got else 'засчитано'}  {'совпадает' if ok else 'РАСХОЖДЕНИЕ'}"
              f"  [{num}] {t[:52]}")
        bad += not ok
    # Знак «меньше» в тексте — не тег. Жадное снятие тегов уносило вместе с
    # ним всё до ближайшего `>`, то есть до начала следующего тега: полторы
    # фразы с числами, и проверка объявляла их потерянными.
    less = "keep it under <13 μmol/L, better <b>10–11</b> μmol.<sup>87</sup>"
    print("  знак «меньше» и номер сноски:")
    for name, cond in (
            ("текст за «<» уцелел", "13" in B.strip(less) and "10–11" in B.strip(less)),
            ("теги при этом сняты", "<b>" not in B.strip(less)),
            ("числа найдены все", set(B._nums(less)) == {"13", "10", "11", "87"}),
            # «0,4.<sup>116</sup>» — точка тут конец фразы, а не разделитель
            # разрядов: склеивать 4 и 116 в 4116 нельзя.
            ("сноска не склеивается с числом",
             set(B._nums("норма 0,4.<sup>116</sup> Дальше текст.")) == {"0", "4", "116"})):
        print(f"    {'совпадает' if cond else 'РАСХОЖДЕНИЕ':10}  {name}")
        bad += not cond
    n = len(COMPOUND) + len(OCR) + len(SPELLED) + len(MEASURE) + len(POWER) + 4
    print(f"\nслучаев: {n}   с расхождениями: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
