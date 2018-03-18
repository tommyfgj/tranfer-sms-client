import re
from config import default_country_code


def real_msisdn(phone_number, country_code=default_country_code):
    try:
        i = int(phone_number)
    except ValueError, err:
        return
    if not i:
        return
    if len(str(i)) < 7:
        return
    if re.compile(r'^\+').search(phone_number):
        return phone_number 
    if re.compile(r'^0').search(phone_number):
        return '+%d%s' % (country_code, phone_number.lstrip('0'))

def local_msisdn(phone_number, country_code=default_country_code):
    msisdn = real_msisdn(phone_number, country_code)
    if msisdn:
        prefix = '+%d' % country_code
        p = len(prefix)
        return '0' + msisdn[p:]


if __name__ == '__main__':
    for s in [
      '0811866584',
      '00811866584',
      '811866584',
      '0213019876',
      '0623019876',
      '+62811866584',
      '+62213019876',
      '+62623019876',
      '62623019876',
      '0815abcd',
      '0888.1819907',
      '0888-1819907',
      'Nomor',
      'AO783']:
      print '%s -> %s -> %s' % (s, real_msisdn(s), local_msisdn(s))
