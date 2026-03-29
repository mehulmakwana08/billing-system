"""Convert numbers to Indian English words (with paise support)"""

_ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
         'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
         'Seventeen', 'Eighteen', 'Nineteen']
_tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _below_hundred(n):
    if n < 20:
        return _ones[n]
    t = _tens[n // 10]
    o = _ones[n % 10]
    return t + (' ' + o if o else '')


def _below_thousand(n):
    if n < 100:
        return _below_hundred(n)
    h = _ones[n // 100] + ' Hundred'
    r = n % 100
    return h + (' ' + _below_hundred(r) if r else '')


def num_to_words(amount):
    """
    Convert a float/int amount to Indian words.
    Example: 47200 -> 'Forty Seven Thousand Two Hundred Only'
    """
    if amount is None:
        return 'Zero Only'

    amount = float(amount)
    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    if rupees == 0 and paise == 0:
        return 'Zero Only'

    parts = []
    n = rupees

    crore = n // 10_000_000;   n %= 10_000_000
    lakh  = n // 100_000;      n %= 100_000
    thou  = n // 1_000;        n %= 1_000
    hund  = n

    if crore:   parts.append(_below_thousand(crore)  + ' Crore')
    if lakh:    parts.append(_below_thousand(lakh)   + ' Lakh')
    if thou:    parts.append(_below_thousand(thou)   + ' Thousand')
    if hund:    parts.append(_below_thousand(hund))

    result = ' '.join(parts) if parts else 'Zero'

    if paise:
        result += f' and {_below_hundred(paise)} Paise'

    return result + ' Only'


if __name__ == '__main__':
    tests = [0, 1, 19, 100, 420, 500, 1000, 33600, 39648, 40592, 47200,
             100000, 174640, 10000000, 12345678]
    for t in tests:
        print(f'{t:>12,} → {num_to_words(t)}')
