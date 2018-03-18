#_*_coding:utf-8_*_
#!/usr/bin/python

#
# GSM & CDMA Modem Handler
# (c)2004-2008,2010,2012 RAB Linux Indonesia
#
# This library can use SMS, USSD, SIM menu, voice call, and phonebook. It
# depend on PySerial and working with AT command.
#

import serial
import termios
import os
import sys
import platform
import re
from types import StringType, UnicodeType, IntType
from time import sleep, time, strftime
from threading import Thread
from msisdn import real_msisdn
from messaging.sms import SmsDeliver, SmsSubmit
import config as conf
import logging
import time
 
from logging.handlers import TimedRotatingFileHandler
from urllib2 import urlopen
import urllib2


###########
# Default #
###########
DEVICE = platform.system() == 'Windows' and 'COM1' or '/dev/ttyUSB0'
REPORT_CENTER = "http://127.0.0.1:9000/decode"
BAUD = 115200
KEEP = False

# Lock filename use minicom standard: /var/lock/LCK..ttyUSB0
# which content is: '     31273 minicom sugiana\n'
def lockFile(device=DEVICE):
    name = 'LCK..%s' % device.split('/')[-1]
    if os.path.exists('/var/lock'):
        return '/var/lock/' + name
    return name

def getUsername():
    return platform.system() == 'Windows' and os.getenv('USERNAME') or \
            os.getenv('USER')
    
def lockContent():
    return '%s modem %s\n' % (str(os.getpid()).rjust(10), getUsername())

def removeFile( filename ):
    try:
        os.remove( filename )
    except OSError:
        pass

def fixMSISDN( s ):
    s = s.strip()
    if s.find('+') > -1:
        return s
    if len(s) < 11:
        return s
    if s[0] == '0':
        return real_msisdn(s)
    return '+' + s

def fixNonASCII( s ):
    r = ''
    for ch in s:
        if ord(ch) > 126:
            r += ' '
        else:
            r += ch 
    return r 
   
def fixUnrecognizeChr( s ):
    r = ''
    for ch in s:
        ascii = ord(ch)
        if ascii < 32:
            r += ' '
        else:
            r += ch 
    return r 

def timestamp(s):
    s = s.strip().replace('<','').replace(' ','')
    if s.find('20') != 0:
        s = '20' + s
    t = s.split('+')
    tz = 7
    if t[1:]:
        try:
            tz = int(t[1])/4
        except ValueError:
            pass
    if not tz:
        tz = 7
    return '%s+%s' % (t[0], tz)


def run(command):
    if command.strip():
        print command
        os.system(command + ' &')

def is_pdu(s):
    try:
        return SmsDeliver(s)
    except:
        return

def get_pid(pid):
    if type(pid) == IntType:
        return pid
    try:
        f = open(pid,'r')
        pid_int = int(f.read().split()[0])
        f.close()
        return pid_int
    except IOError:
        return
    except ValueError:
        return
    except IndexError:
        return
 
def isLive(pid):
    pid = get_pid(pid)
    if not pid:
        return
    try:
        os.kill(pid, 0)
    except OSError:
        return


class Modem:
    def __init__(self, device, baud, logger):
        self.logger = logger
        self.__initVars(device)
        pgid = isLive(self.lockFile)
        if pgid:
            err = '%s already locked by pid %s' % (device, pgid)
            self.log( err )
            self.pid = self.serial = None
            return
        f = open(self.lockFile,'w')
        f.write(lockContent())
        f.close()
        self.pid = os.getpid()
        try:
            self.serial = serial.Serial(device, baud, timeout=1)
            self.log('%s baudrate %s' % (device, baud))
            self.standardInit()
        except serial.serialutil.SerialException, msg:
            self.log( msg )
            self.serial = None
        except termios.error, msg:
            msg = '%s busy' % device
            self.log( msg )
            self.serial = None
        if not self.serial:
            return removeFile( self.lockFile )

    def __initVars(self, device):
        self.reset_software_done = False
        self.sim_app_config_done = False
        self.sim_app_enable_done = False
        self.busy = False
        # Hardware information
        self.hw = {'device': device, 'gsm': True}
        self.inbox = [] # New sms
        self.sms = {}
        # 0: sending message, > 0: message sent, < 0: sending failed
        self.sent = -1
        self.delivered = [] # Message sent delivered time
        self.voice = {}
        self.phone = {}
        self.phoneStructure = {}
        self.signal = {'quality': 0}
        self.card = {} # SIM Card Information 
        self.gprs = {}
        self.operators = {}
        self.STIN = 0 # SIM ToolKit Indication
        self.ERROR = False
        self.menu = {}
        self.initUSSD = False
        self.initProduct = False
        self.merk = ''
        self.initMenu = False
        self.menuEnable = False
        self.smsFormat = SMS_TEXT
        self.smsLong = {}
        self.lockFile = lockFile(device)


    def log(self, msg):
        self.logger.info(msg)

    ########################
    # Serial port handlers #
    ########################
    def device(self):
        if self.serial:
            return self.serial._port

    def close(self):
        if not self.serial:
            return
        self.serial.close()
        self.serial = None
        try:
            os.remove(self.lockFile)
        except OSError, msg:
            pass


    #######################
    # AT Command Handlers #
    #######################
    def write(self, at):
        if not self.serial:
            return
        self.busy = True
        self.log('-> '+at)
        try:
            # Use str() as non Unicode stream
            self.serial.write(str(at))
        except OSError, err:
            self.log( err )
            self.close()
            self.ERROR = True

    def send(self, at, end='\r', seconds=1):
        self.write(at+end)
        sleep(seconds)
        return self.unbusy(self.readlines())

    def readlines(self):
        if not self.serial:
            return []
        try:
            lines = self.serial.readlines()
        except:
            self.log(sys.exc_info()[1])
            self.close()
            self.ERROR = True
            return []
        for line in lines:
            s = line.strip()
            if s:
                self.log('<- '+s)
            t = self.parser(s)
            self.onRead(t, s)
        return lines

    def line2tokens(self, line):
        # +CGDCONT: 1,"IP","satgprs",,0,0
        # +CLIP: "+628881162123",145
        # +CMGL: 1,"REC READ","+628881162123",,"06/11/22,15:54:48+28"
        # +CMGR: "REC UNREAD","818","Cust. Service","08/06/22,19:28:41+28"
        # +CMGS:3 --> CDMA sometimes does not include a space after the colon. 
        t = []
        line = line[1:] # Remove plus sign
        s = ''
        quote = False
        lastCh = ''
        while line:
            ch = line[0]
            if not t and ch in [':','=']:
                t.append(s) # First token
                s = ''
                if line[1:] == ' ':
                    line = line[1:] # Remove space char 
            elif ch == '"':
                if quote:
                    quote = False
                    t.append(s)
                    s = ''
                else:
                    quote = True
            elif quote:
                s += ch
            elif ch == ',':
                if s:
                    if not quote and s[0] in NUMBERS:
                        try:
                            s = int(s)
                        except ValueError:
                            return t
                    t.append(s)
                    s = ''
                if lastCh == ',':
                    t.append('')
            else:
                s += ch
            line = line[1:]
            lastCh = ch
        if s:
            if not quote and s[0] in NUMBERS:
                try:
                    s = int(s)
                except ValueError:
                    return t
            t.append(s)
        # ' 99' to 99
        if t[1:] and type(t[1]) in [StringType, UnicodeType] and t[1] \
            and t[1][0] == ' ':
            try:
                t[1] = int(t[1])
            except ValueError:
                pass
        return t 

    def on_(self, line):
        t = self.line2tokens( line )
        if not t:
            return
        fungsi = 'on_%s' % t[0]
        try:
            func = getattr(self, fungsi)
        except AttributeError, err:
            return t
        func( t[1:] )
        return t 

    def on_CGSN(self, tokens):
        # Motorolla C381, Ainul Hakim <smartikon@yahoo.com>
        # +CGSN: IMEI353113004744151
        if type(tokens[0]) == type(''):
            self.hw['sn'] = tokens[0].replace('IMEI','').strip()
        else:
            # CDMA modem, Ainul Hakim <smartikon@yahoo.com>
            # +CGSN: 00000000
            self.hw['sn'] = tokens[0]

    # Motorolla C381, Ainul Hakim <smartikon@yahoo.com>
    # CDMA Multitech
    def on_CIMI(self, tokens):
        # +CIMI: 510111009257962
        self.card['imei'] = str(tokens[0]).strip()

    # Motorolla C381, Ainul Hakim <smartikon@yahoo.com>
    def on_CGMM(self, tokens):
        # +CGMM: "GSM900","GSM1800","GSM1900","MODEL=C380"
        if tokens:
            self.hw['product'] = self.merk = \
                tokens[-1].replace('MODEL=','').strip()

    def on_CLIP(self, tokens):
        # +CLIP: "+628881162123",145
        # +CLIP: "+628159126773",145,,,"Os 298"
        self.voice['from'] = tokens[0] 
        if tokens[4:]:
            self.voice['name'] = tokens[4]
        if 'call' in conf.event:
            run(conf.event['call'])

    def on_CMTI(self, tokens):
        # tokens = ['SM',3]
        # tokens = ['MT',30,0]
        index = tokens[1]
        self.inbox.append(index)
        if 'sms' in conf.event:
            run(conf.event['sms'])

    # Message List
    def on_CMGL(self, tokens):
        # +CMGL: 1,"REC READ","+628881162123",,"06/11/22,16:08:42+28"
        # +CMGL: 2,"STO SENT","4444",,
        # +CMGL: 4,"INVALID MESSAGE"
        if not tokens[2:]:
            return
        index = int(tokens[0])
        self.sms[index] = {
          'msisdn'  : fixMSISDN(tokens[2]),
          'group'   : tokens[1]}
        if tokens[3:] and self.sms[index].has_key('time'):
            self.sms[index]['time'] = '20%s%s' % (
              tokens[3][:-2],
              int(tokens[3][-2:])/4)
        return index

    # Message Sent
    def on_CMGS(self, tokens):
        # tokens = [248]
        self.sent = tokens[0] # Message ID

    # Message Delivered
    def on_CDS(self, tokens):
        # Year value from Mobile8 is 10, should be 2010
        # +CDS:2,1,"08179140068",129,"10/02/24,03 :03 :58","10/02/24,03 :04 :02",32768
        self.delivered.append({'id': tokens[1], # Message ID (see on_CMGS)
                               'msisdn': fixMSISDN(tokens[2]),
                               'time': timestamp(tokens[4])
                               })

    def on_CSQ(self, tokens):
        # received signal strength indication
        self.signal['rssi'] = int(str(tokens[0]).strip())
        # bit error rate
        self.signal['ber'] = int(str(tokens[1]).strip())
        if self.signal['rssi'] == 99:
            self.signal['quality'] = 0 
        else:
            self.signal['quality'] = self.signal['rssi']*100.0/31

    # Operator ID
    def on_COPS(self, tokens):
        # +COPS: 0,2,51011
        # +COPS: 0
        if not tokens[2:]:
            return
        id = tokens[2]
        self.card['operator'] = id
        if self.operators.has_key(id):
            self.card['name'] = self.operators[id] 

    def on_CGCLASS(self, tokens):
        # +CGCLASS: "B"
        self.gprs['class'] = tokens[0]

    def on_CGDCONT(self, tokens):
        # +CGDCONT: 1,"IP","satgprs",,0,0
        # +CGDCONT: 1,IP,indosatm2,0.0.0.0,0,0
        self.gprs['cid'] = tokens[0]
        self.gprs['type'] = tokens[1]
        self.gprs['apn'] = tokens[2]
        if tokens[3:]:
            self.gprs['address'] = tokens[3]

    # Phonebook
    def on_CPBR(self, tokens):
        # +CPBR: (1-250),20,14
        # +CPBR: 1,"818",129,"Cust. Service"
        if type(tokens[0]) == type(''):
            begin, end = tokens[0].strip()[1:-1].split('-')
            self.phoneStructure['begin'] = int(begin)
            self.phoneStructure['end'] = int(end) 
            self.phoneStructure['msisdn'] = int(tokens[1])
            self.phoneStructure['name'] = int(tokens[2])
        else:
            index = tokens[0]
            self.phone[index] = [tokens[1], tokens[3]]

    def on_STSF(self, tokens):
        # +STSF: (0-2),(160060C01F-5FFFFFFF7F),(1-255),(0-1)
        self.menuEnable = True

    def on_STGR(self, tokens):
        self.STIN = 0

    def on_STIN(self, tokens):
        # tokens = [0]
        self.STIN = tokens[0]
        self.menu = {}

    def on_STGI(self, tokens):
        # +STGI: ""
        # +STGI: 0
        # +STGI: "Layanan Data"
        # +STGI: 2,11,"MyInfo",0
        # +STGI: 0,"MyInfo"
        # +STGI: 1,"Konfirmasi, Isi Pulsa ke 085643272634 sejumlah 5,000 pada REGULER",1
        # +STGI: 1,"Message Sent",0
        index = 0
        label = ''
        for t in tokens:
            if type(t) == type(0):
                if not index:
                    index = t
            else:
                label = t
        self.menu[label] = index

    # Operator list
    def on_COPN(self, tokens):
        # +COPN: 51011,"XL"
        id = tokens[0]
        self.operators[id] = tokens[1]

    def AT_line2tokens(self, line):
        # AT+CMGS
        # AT+CPBW=240
        # AT+CPBW=10,"+6285219892489",129,"trimanto dkp"
        t = []
        line = line[3:] # Remove AT+
        s = ''
        quote = False
        while line:
            ch = line[0]
            if not t and ch == '=':
                # Token pertama
                t.append(s)
                s = ''
            elif ch == '"':
                if quote:
                    quote = False
                    t.append(s)
                    s = ''
                else:
                    quote = True
            elif quote:
                s += ch
            elif ch == ',':
                if s:
                    t.append(int(s))
                    s = ''
            else:
                s += ch
            line = line[1:]
        if s:
            if not quote and s[0] in NUMBERS:
                try:
                    s = int(s)
                except ValueError:
                    pass
            t.append(s)
        return t

    def onAT_(self, line):
        tokens = self.AT_line2tokens(line)
        if not tokens:
            return
        fungsi = 'onAT_%s' % tokens[0] 
        try:
            func = getattr(self, fungsi)
        except AttributeError, err:
            return
        func( tokens[1:] )

    def onAT_CGMM(self, tokens):
        self.initProduct = True

    def onAT_CUSD(self, tokens):
        # AT+CUSD=1
        self.initUSSD = True

    def onAT_CMGD(self, tokens):
        # AT+CMGD=4
        i = int(tokens[0])
        if self.sms.has_key(i):
            del self.sms[i]
        try:
            self.inbox.remove(i)
        except ValueError, err:
            return

    def onAT_CMGR(self, tokens):
        # AT+CMGR=4
        try:
            self.inbox.remove(tokens[0])
        except ValueError:
            pass

    #def onAT_CMGF(self, tokens):
    #    self.smsFormat = tokens[0]

    def onAT_CMGS(self, tokens):
        # AT+CMGS
        self.sent = -1

    def onAT_CPBW(self, tokens):
        # AT+CPBW=240
        # AT+CPBW=10,"+6285219892489",129,"trimanto dkp"
        i = int(tokens[0])
        if self.phone.has_key(i):
            del self.phone[i]

    def onAT_STGI(self, tokens):
        if tokens[0] == 1:
            self.STIN = 0

    def onRead(self, tokens, line):
        pass

    def parser(self, line):
        if not line:
            return
        self.ERROR = line.strip() == 'ERROR'
        if line == 'RING' and not self.voice:
            self.voice['ring'] = strftime('%Y-%m-%d %H:%M:%S')
            return
        if line in ['ATH','NO CARRIER','ERROR']:
            self.voice = {}
            return
        if line == 'ATA':
            del self.voice['ring']
            return
        if re.compile(r'^ATD').search(line) and line.find('*') < 0:
            self.voice['to'] = line[3:].strip(';')
            return
        if line.find('AT+') == 0:
            return self.onAT_( line )
        if line[0] == '+':
            return self.on_( line )

    def sendExpect(self, at_command, expect='OK', end='\r'):
        self.write(at_command+end)
        r = self.readExpect( expect )
        return self.unbusy(r)

    def readExpect(self, expect):
        self.busy = True
        start = time.time()
        while time.time() - start < TIMEOUT:
            for line in self.readlines():
                line = line.strip()
                if line == expect:
                    return line
                r = self.parser(line)
                if r and str(r[0]).strip() == expect:
                    return r

    def unbusy(self, r=None):
        self.busy = False
        return r

    ################ 
    # Any handlers #
    ################ 
    def standardInit(self):
        self.cancelInput()
        self.hangup()
        self.getProduct()

    def cancelInput(self):
        self.send(chr(26))

    def hangup(self):
        # CDMA modem will generate an error if ATH is called when NOT in a call
        # session. Ignore.
        self.send('ATH')
        self.voice = {}

    def defaultConf(self):
        self.send('ATZ')

    def factorySetting(self):
        self.send('AT&F')

    def speaker(self, on=True):
        if on:
            value = 1
        else:
            value = 0
        self.send('AT+SPEAKER=%s' % value)

    def signalQuality(self):
        self.send('AT+CSQ')

    def resetSoftware(self):
        if not self.reset_software_done:
            self.send('AT+CFUN=1')
            self.reset_software_done = True
            self.smsMode()

    def reset(self):
        self.cancelInput()
        self.hangup()

    """
    Ideal condition:

    2009-04-24 15:33:20,083 INFO -> AT+CUSD=1,"*101*5000*081915706867*1234#"
    2009-04-24 15:33:21,115 INFO <- AT+CUSD=1,"*101*5000*081915706867*1234#"
    2009-04-24 15:33:21,115 INFO <- OK
    2009-04-24 15:33:25,115 INFO <- +CUSD: 1,"Transaksi Anda telah diterima dan sedang diproses. Status transaksi Anda akan dikirim melalui SMS...",0

    Sms alert when there are incoming USSD:

    2009-04-24 15:32:20,504 INFO -> AT+CUSD=1,"*101*10000*0818501070*1234#"
    2009-04-24 15:32:22,352 INFO <- AT+CUSD=1,"*101*10000*0818501070*1234#"
    2009-04-24 15:32:22,352 INFO <- OK
    2009-04-24 15:32:22,352 INFO <- +CMTI: "SM",1
    2009-04-24 15:32:24,396 INFO <- +CUSD: 1,"Transaksi Anda telah diterima dan sedang diproses. Status transaksi Anda akan dikirim melalui SMS...",0
    """
    def ussd(self, ussd_command):
        if not self.initUSSD:
            self.send('AT+CUSD=1')
        if self.merk in conf.ussd_use_atd:
            at_command = 'ATD%s;' % ussd_command
        else:
            at_command = 'AT+CUSD=1,"%s"' % ussd_command
        self.write(at_command + '\r')
        start = time.time()
        msg = None
        msgLine = False
        while True:
            for line in self.readlines():
                line = line.strip()
                # +CUSD: 4
                # +CUSD: 2,"Nomor tujuan terkunci",15
                # +WFNM="ACTIVE, Pulsa: Rp.359.4, Masa Grace: 28/02/2012"
                # +WCNT:3
                if msg is None and line.find('+CMTI') == 0:
                    continue
                msgLine = msgLine or \
                          line.find('+CUSD') == 0 or \
                          line.find('+WFNM') == 0 or \
                          line.find('+WCNT') == 0 or \
                          line.find('+WEND') == 0 or \
                          line != at_command.strip()
                if not msgLine:
                    continue
                # Di CDMA kadang USSD adalah call
                if line.find('+WCNT') == 0 or line.find('+WEND') == 0:
                    return self.unbusy({'message': ''})
                for ch in line:
                    if ch == '"':
                        if msg is None:
                            msg = ''
                            continue
                        else:
                            return self.unbusy({'message':msg})
                    if type(msg) == type(''):
                        msg += ch
            sleep(1)
            if time.time() - start >= TIMEOUT:
                return self.unbusy({'code': 6})

    def getOperator(self, fromList=False):
        if fromList:
            f = open(OPERATOR_LIST_FILE)
            for line in f.readlines():
                t = line.strip().split('\t')
                if t[1:]:
                    code, name = t[:2]
                    self.operators[int(code)] = name
            f.close()
        elif not self.hw['gsm']:
            return
        else:
            self.send('AT+COPN')
        self.send('AT+COPS?')
        return self.card
 
    # SIM card information
    def getIMEI(self):
        self.write('AT+CIMI\r')
        for line in self.readlines():
            t = line.strip()
            try:
                int(t)
                self.card['imei'] = t
                return self.unbusy(t)
            except ValueError:
                pass
        if 'imei' in self.card:
            return self.unbusy(self.card['imei'])

    # Modem information
    # SAMSUNG SGH-L700 containing space, 
    """
    <- AT+CGSN
    <- 357805 02 129863 0
    <- OK
    """
    def getSN(self):
        self.write('AT+CGSN\r')
        for line in self.readlines():
            t = line.strip()
            try:
                t = t.replace(' ','')
                int(t)
                self.hw['sn'] = t
                return self.unbusy(t)
            except ValueError:
                pass

    def getProduct(self):
        if self.initProduct:
            return self.merk
        self.write('AT+CGMM\r')
        for line in self.readlines():
            t = line.strip()
            if t not in ['AT+CGMM','ERROR']:
                if not self.merk: 
                    self.hw['product'] = self.merk = t
                return self.unbusy(t)
        self.write('ATI0\r')
        s = []
        for line in self.readlines():
            t = line.strip()
            if t not in ['ATI0','OK']:
                s.append(t)
        s = ' '.join(s)
        if not self.merk:
            self.hw['product'] = self.merk = s.strip()
        self.hw['gsm'] = False
        return self.unbusy(s)

    def tryMenu(self):
        if self.initMenu:
            return
        self.send('AT+STSF=?')
        self.initMenu = True

    def getGPRS(self):
        if self.hw['gsm']:
            self.send('AT+CGCLASS?')
            self.send('AT+CGDCONT?')
            return self.gprs

    # Activate CMTI alert when incoming SMS
    def smsSignal(self):
        if self.merk in conf.init_sms:
            at_command = 'AT+CNMI=%s' % conf.init_sms[self.merk]
            self.send( at_command )
        else:
            self.send('AT+CNMI=0,1,1,1,0')

    # Some modem need SMS center like ZTE (Agus Supriadi)
    def smsCenter(self):
        if 'imei' not in self.card:
            return
        if self.card['imei'] not in conf.smsc:
            return
        self.send('AT+CSCA="%s"' % conf.smsc[self.card['imei']])

    def sendCMGF(self, code=1):
        if not self.hw['gsm']:
            return
        if self.sendExpect('AT+CMGF=%d' % code) == 'OK':
            self.smsFormat = code
        else:
            self.smsFormat = SMS_TEXT

    def smsTextMode(self):
        self.sendCMGF()

    def smsPduMode(self):
        self.sendCMGF(0)

    def smsMode(self):
        #if self.merk in conf.pdu_mode:
        #    self.smsPduMode()
        #else:
        #    self.smsTextMode()
        self.smsPduMode()

    def smsMemory(self):
        if self.merk in conf.memory_sms:
            # ['MT','SM'] -> ['"MT"','"SM"']
            quote = map(lambda x: '"%s"' % x, conf.memory_sms)
            at = 'AT+CPMS=%s' % ','.join(quote)
            self.send(at)

    def initsms(self):
        self.smsSignal()
        self.smsCenter()
        self.smsMode()
        self.smsMemory()

    def putsms(self, MSISDN, Message):
        Message = fixNonASCII( Message )
        if self.merk in conf.unrecognize_character:
            Message = fixUnrecognizeChr( Message )
        if self.smsFormat == SMS_TEXT:
            if self.merk in conf.sms_dest_without_sign:
                MSISDN = MSISDN.lstrip('+')
            self.send('AT+CMGS="%s"' % MSISDN)
            Message = Message[:160]
            self.sendExpect(Message, 'CMGS', end=chr(26))
        else:
            self.log('KIRIM SMS %s' % {'msisdn': MSISDN, 'message': Message})
            for pdu in SmsSubmit(MSISDN, Message).to_pdu():
                self.send('AT+CMGS="%s"' % pdu.length)
                Message = pdu.pdu
                self.sendExpect(Message, 'CMGS', end=chr(26))
        return self.unbusy(self.sent)

    """
    -> AT+CMGR=1
    <- +CMGR: "REC READ","+628159126773",,"05/12/06,11:07:11+28"
    <- Satu
    <- Dua
    <-
    <- OK
    """
    """
    Telkomsel chip (Davindo):
    -> AT+CMGR=4
    <- +CMGR: "REC UNREAD","0816122",,"09/02/21,19:51:06+1<"
    <- Anda telah dihubungi oleh
    <- +6281586285370 ,
    <- OK
    """
    """
    Handphone Motorola C381 (Ikonpulsa):
    <- +CMTI: "MT",8550
    -> AT+CMGR=8550
    <- AT+CMGR=8550
    <- +CMGR: "REC UNREAD", "+6287832061779", "2009/4/2,18:26:0"
    <- test cnmi, incoming alert 2
    <- OK
    ['CMGR', 'REC UNREAD', ' +6287832061779', ' 2009/4/2,18:26:0', '\r\n']
    """
    """
    Multitech CDMA modem
    <- +CMTI:"MT",0,0
    -> AT+CMGR=0
    <- AT+CMGR=0
    <- +CMGR:"REC UNREAD","08179140068","10/02/24,05 :22 :48",1,2,0,"08179140068",19
    <- Ugr pesen bigmac ya
    <- OK
    """

    def smsindex(self, index):
        self.write('AT+CMGR=%s\r' % index)
        EOF_SIGN = ['\r\n\r\nOK\r\n','OK\r\n']
        EOF_COUNT = []
        for e in EOF_SIGN:
            EOF_COUNT.append(len(e))
        EOF = False
        s = ''
        r = {}
        start = time.time()
        while not EOF and (time.time() - start < TIMEOUT):
            for line in self.readlines():
                s += line
                i = -1 
                for e in EOF_SIGN:
                    i += 1
                    EOF = s[-EOF_COUNT[i]:] == e 
                    if EOF:
                        break
                if EOF:
                    break
                if r.has_key('message'):
                    r['message'] += line
                elif line.strip().find('+CMGR') == 0:
                    r['message'] = ''
                    if self.smsFormat == SMS_TEXT:
                        tokens = self.line2tokens( line.strip() )
                        r['msisdn'] = fixMSISDN(tokens[2])
                        r['group'] = tokens[1]
                        for t in tokens[3:]:
                            if t.find('/') > -1: 
                                r['time'] = timestamp(t)
                                break
            sleep(1)
        if r.has_key('message'):
            r['message'] = r['message'].strip()
            pdu = is_pdu(r['message'])
            if pdu:
                if 'ref' in pdu.data:
                    ref = pdu.data['ref']
                    if ref in self.smsLong:
                        self.smsLong[ref][pdu.data['seq']] = pdu.text
                        if len(self.smsLong[ref].keys()) != pdu.data['cnt']:
                            return self.unbusy(r)
                        keys = self.smsLong[ref].keys()
                        keys.sort()
                        text = []
                        for key in keys:
                            text.append(self.smsLong[ref][key])
                        r['message'] = ''.join(text)
                        del self.smsLong[ref]
                    else:
                        self.smsLong[ref] = { pdu.data['seq']: pdu.text }
                        return self.unbusy(r)
                else:
                    r['message'] = pdu.text
                r['msisdn'] = pdu.number
                r['time'] = '%s+0' % pdu.date
                self.log('Receive SMS %s' % r)
            else:
                self.smsFormat == SMS_TEXT
        self.sms[index] = dict(r)
        return self.unbusy(r)

    """
    -> AT+CMGL="REC UNREAD"
    <- +CMGL: 1,"REC UNREAD","+628170944042",,"05/02/13,08:07:36+28"
    <- Satu
    <- Dua
    <- Tiga
    <- +CMGL: 2,"REC UNREAD","+628170944042",,"05/02/13,08:16:44+28"
    <- Minggu
    <- Senin
    <- Selasa
    <-
    <- OK
    
    -> AT+CMGL="REC UNREAD"    
    <- OK
    
    """       

    # PDU mode
    """
    <- AT+CMGL=4
    <- +CMGL: 1,1,,27
    <- 0791268808006000240C9126120432684600001170510161148209E3B7380C6287CF69
    <- +CMGL: 2,1,,23
    <- 0791268808006000240C9126120432684600001170510112738204E3B7380C
    <- OK
    """

    # CDMA
    """
    <- AT+CMGL="ALL"
    <- +CMGL:0,"REC READ","808",0,2,65
    <- (25)Jumlah pulsa jual anda pada tanggal 25/03/12 13:40 sebesar 0.
    <- +CMGL:1,"REC UNREAD","999",0,2,107
    <- Pulsa Anda Rp 0,berlaku s/d 07/03/2013.Bonus Pulsa Rp 4,900,s/d 07/03/2013.Beli konten di wap.smartfren.com
    <- +CMGL:2,"REC READ","999",0,2,107
    <- Pulsa Anda Rp 0,berlaku s/d 07/03/2013.Bonus Pulsa Rp 4,900,s/d 07/03/2013.Beli konten di wap.smartfren.com
    <- +CMGL:3,"REC READ","08179140068",0,2,8
    <- Sal 1234
    <- +CMGL:4,"REC READ","999",0,2,107
    <- Pulsa Anda Rp 0,berlaku s/d 07/03/2013.Bonus Pulsa Rp 4,900,s/d 07/03/2013.Beli konten di wap.smartfren.com
    <- OK
    """
    def smsall(self):
        def set_text():
            self.sms[index]['msisdn'] = fixMSISDN(msisdn)
            self.sms[index]['message'] = msg.strip()
            if smsdate:
                self.sms[index]['time'] = smsdate 

        def set_pdu():
            try:
                pdu = SmsDeliver(msg.strip())
            except ValueError, e:
                # IM3 pernah mengirim '00/00/12 05:27:14' (Kokocell)
                self.log(e)
                return
            self.sms[index]['msisdn'] = pdu.number
            self.sms[index]['time'] = '%s+0' % pdu.date
            self.sms[index]['message'] = pdu.text
            self.log('Receive %s' % self.sms[index])

        def set_sms():
            if self.smsFormat == SMS_TEXT:
                set_text()
            else:
                set_pdu()

        self.smsFormat = SMS_PDU #force to chose pdu mode 
        at = self.smsFormat == SMS_PDU and 4 or '"ALL"'
        self.write('AT+CMGL=%s\r' % at)
        index = None
        msg = ''
        start = time.time()
        EOF_SIGN = '\r\n\r\nOK\r\n'
        EOF_COUNT = len(EOF_SIGN)
        s = ''
        while True:
            for line in self.readlines():
                if not index and line.strip() in ['OK','ERROR']: # empty ?
                    return self.unbusy(self.sms)
                s += line
                if s[-EOF_COUNT:] == EOF_SIGN: # output berakhir ?
                    if index:
                        set_sms()
                        return self.unbusy(self.sms)
                if self.smsFormat == SMS_TEXT:
                    for pola in SMS_ALL_PATTERNS: 
                        match = pola.search(line)
                        if match:
                            break
                else:
                    match = re.compile(r'^\+CMGL: (.*),(.*),,(.*)').search(line)
                if match:
                    if index:
                        set_sms()
                    index = int(match.group(1))
                    msg = ''
                    if self.smsFormat == SMS_TEXT:
                        msisdn = match.group(3)
                        try:
                            timezone = int(match.group(9))/4
                        except ValueError:
                            timezone = 7
                        except IndexError:
                            timezone = None
                        smsdate = timezone and '20%s,%s%s%s' % (match.group(5),
                                  match.group(7), match.group(8), timezone)
                else:
                    msg += line
            sleep(1)
            if time.time() - start >= TIMEOUT:
                if index:
                    set_sms()
                return self.unbusy(self.sms)

    def getsms(self, index=None):
        if index is not None:
            return {index: self.smsindex(index)}
        
        result = self.smsall()
        for index in result: 
            if 'time' not in result[index]:
                result[index] = self.smsindex(index)
        return result


    def delsms(self, i):
        self.send('AT+CMGD=%s' % i)
        return self.unbusy()

    ###################
    # SIM Application #
    ###################
    def getMenuConfirm(self):
        self.write('AT+STGI=%s\r' % self.STIN)
        start = time.time()
        index = 0
        label = ''
        EOF_SIGN = '\r\n\r\nOK\r\n'
        EOF_COUNT = len(EOF_SIGN)
        s = ''
        while time.time() - start < TIMEOUT:
            for line in self.readlines():
                s += line
                if s[-EOF_COUNT:] == EOF_SIGN:
                    return self.unbusy()
                if time.time() - start >= TIMEOUT:
                    self.menu = {label: index}
                    return self.unbusy()
                if label:
                    t = line.split('"')
                    label += t[0]
                    if t[1:]:
                        self.menu = {label: index}
                        return self.unbusy()
                elif line.strip().find('+STGI') == 0:
                    tokens = self.line2tokens( line )
                    label = tokens[2]
            sleep(0.1)
        self.menu = {label: index}
        return self.unbusy()

    def getMenu(self):
        if self.STIN == 1:
            return self.unbusy(self.getMenuConfirm())
        if self.STIN in [98,99]:
            self.STIN = 0
        self.write('AT+STGI=%s\r' % self.STIN)
        start = time.time()
        while time.time() - start < 3:
            for line in self.readlines():
                line = line.strip()
                if line == 'OK':
                    return self.unbusy()
        return self.unbusy()

    def selectMenu(self, index):
        if self.STIN == 6:
            cmd = 6
        else:
            cmd = 0
        return self.send('AT+STGR=%s,1,%s' % (cmd, index), seconds=0.1)

    def giveData(self, data):
        self.send('AT+STGR=3,1', seconds=0.1)
        self.sendExpect(data, 'STIN', end=chr(26))

    def giveOK(self):
        self.sendExpect('AT+STGR=1', 'STIN')

    def justWait(self, msg='Wait'):
        print msg
        self.busy = True
        self.STIN = None
        start = time.time()
        while time.time() - start < TIMEOUT: 
            for line in self.readlines():
                if self.STIN != None:
                    return

    def autoSelectMenu(self, items):
        if not items:
            return
        label = items[0]
        if self.STIN == 3:
            self.giveData(label)
        elif label == 'OK': 
            self.giveOK()
        else:
            if not label in self.menu:
                print label, 'not found'
                return
            index = self.menu[label]
            self.selectMenu(index)
        self.getMenu()
        if self.STIN == 9:
            print 'Send SMS'
        elif self.STIN == 10:
            self.justWait('Send USSD')
        self.autoSelectMenu(items[1:])

    def simAppConfig(self):
        if not self.sim_app_config_done:
            self.send('AT+STSF=2,"5FFFFFFF7F",%s,0' % (TIMEOUT/10))
            self.sim_app_config_done = True

    def simAppEnable(self):
        if not self.sim_app_enable_done:
            self.send('AT+STSF=1', seconds=2)
            self.sim_app_enable_done = True

    def simApp(self, items):
        self.tryMenu()
        if not self.menuEnable:
            return self.unbusy('ERROR: This modem can not use SIM card menu.')
        self.simAppConfig()
        self.resetSoftware()
        self.simAppEnable()
        while not self.menu:
            self.getMenu()
        self.autoSelectMenu(items)
        m = []
        for label in self.menu.keys():
            m.append([self.menu[label], label])
        m.sort()
        s = ''
        for index, label in m: 
            s += label + ','
        return self.unbusy(s.strip(','))

    #############
    # Phonebook #
    #############
    def getPhone(self):
        self.send('AT+CPBS="SM"')
        self.send('AT+CPBR=?')
        self.send('AT+CPBR=%s,%s' % (
          self.phoneStructure['begin'],
          self.phoneStructure['end']))

    def addPhone(self, index, msisdn, name):
        self.send('AT+CPBW=%s,"%s",129,"%s"' % (index, msisdn, name))
        self.phone[index] = [msisdn, name]

    def delPhone(self, index):
        self.send('AT+CPBW=%s' % index)

    #########
    # Voice #
    #########
    def voiceCall(self, MSISDN):
        print 'Call', MSISDN
        self.send('ATD%s;' % MSISDN)

    def sendDTMF(self, ch):
        if ch in DTMF_KEYS: 
            self.send('AT+VTS=%s' % ch)
            return True
     
    def answerCall(self):
        print 'Answering call'
        self.send('ATA')

    def showIncomingMSISDN(self):
        self.send('AT+CLIP=1')
            
    def showMyMSISDN(self, on=True):
        if on:
            value = 2 
        else:
            value = 0
        self.send('AT+CLIR=%s' % value)
            

class Idle(Thread):
    def __init__(self):
        self.running = True
        Thread.__init__(self)

    def run(self):
        while self.running:
            if m.busy:
                continue
            m.readlines()
            if m.inbox:
                i = m.inbox[0]
                m.smsindex(i)
            elif m.STIN == 1:
                m.getMenu()

    def stop(self):
        self.running = False


def commandSMS( command ):
    t = command.split()
    index = t[0][1:]
    if index:
        index = int(index)
    if not m.sms:
        m.getsms()
    if t[1:] and t[1][0].upper() == 'D':
        m.delsms( index )
    elif index:
        show( m.getsms(index) )
    else:
        show(m.sms)

def availablePhoneIndex():
    for i in range(m.phoneStructure['begin'], m.phoneStructure['end']):
        if not m.phone.has_key(i):
            return i

def phoneSearch( search ):
    search = search.upper()
    for i in m.phone.keys():
        msisdn, name = m.phone[i]
        if name.upper().find(search) > -1:
            show({i: m.phone[i]}) 

def commandPhone( command ):
    t = command.split()
    index = t[0][1:]
    if index:
        index = int(index)
    if not m.phone:
        m.getPhone()
    if len(command) == 1:
        return show(m.phone)
    if t[1:] and t[1][0] in MSISDN_NUMBERS:
        msisdn = t[1]
    else:
        msisdn = None
    if not index and t[1:] and not msisdn: 
        return phoneSearch( t[1] )
    if t[2:] and msisdn: 
        if not index: 
            index = availablePhoneIndex()
        if index:
            name = ' '.join(t[2:])
            m.addPhone(index, msisdn, name) 
        else:
            print 'Phonebook full'
    elif t[1:] and t[1][0].upper() == 'D':
        m.delPhone( index )
    elif index:
        msisdn = m.phone[index][0]
        if t[1:]:
            msg = ' '.join(t[1:])
            m.putsms( msisdn, msg )
        else:
            m.voiceCall( msisdn )

def commandApp( command ):
    t = command.split()
    if t[1:]:
        s = ' '.join(t[1:])
        items = s.split(',')
    else:
        items = []
    m.simApp(items)


HELP_COMMAND = """
Voice call     : <msisdn>
Send SMS       : <msisdn> <message>
Read SMS       : S[index]
Remove SMS     : S<index> D 
Phone list     : P
Write phone    : P[index] <msisdn> <name>
Delete phone   : P<index> D
Search phone   : P <name>
Call phone     : P<index>
Send SMS       : P<index> <message>
SIM application: A
"""

def waitForCommand():
    daemon = Idle()
    daemon.start()
    print 'Type H for help.'
    while True: 
        sleep(1)
        print 'Idle.'
        command = raw_input()
        while m.busy:
            pass
        if m.voice:
            if 'rin' in m.voice:
                m.answerCall()
            else:
                if command:
                    m.sendDTMF(command[0])
                else:
                    m.hangup()
        elif not command:
            break
        elif command[0].upper() == 'H':
            print HELP_COMMAND
        elif command[0].upper() == 'S':
            commandSMS( command )
        elif command[0].upper() == 'P':
            commandPhone( command )
        elif command[0].upper() == 'A':
            commandApp( command )
        elif command[0] == '*':
            m.ussd( command )
        else:
            t = command.split()
            msisdn = t[0]
            if t[1:]:
                msg = ' '.join(t[1:])
                m.initsms()
                m.putsms(msisdn, msg)
            else:
                m.voiceCall( msisdn )
    daemon.stop()
    print 'Selesai'

def voiceCall( msisdn ):
    m.showMyMSISDN()
    m.voiceCall( msisdn )
    print 'Press ENTER to finish.'
    raw_input()
    m.hangup()

def operator():
    m.getSN()
    m.getOperator()
    m.getIMEI()
    m.getGPRS()
    m.signalQuality()
    show(m.hw)
    show(m.card)
    show(m.gprs)
    show(m.signal)
    s = ''
    for opcode in m.operators.keys():
        s += '\n%s\t%s' % (opcode, m.operators[opcode])
    f = open(OPERATOR_LIST_FILE, 'w')
    f.write(s.strip())
    f.close()

def getsms( DIR ):
    m.getIMEI()
    m.getsms()
    #show(m.sms)
    for i in m.sms.keys():
        content = 'imei: %s\ngroup: %s' % (
            m.card['imei'], m.sms[i]['group'])
        if 'time' in m.sms[i]:
            content += '\ntime: %s' % m.sms[i]['time']
        if 'msisdn' in m.sms[i]:
            content += '\nmsisdn: %s' % m.sms[i]['msisdn']
        content += '\n\n%s' % m.sms[i]['message']
        # The file name must be unique, but also not the case two files with the
        # same SMS.
        if 'time' in m.sms[i]:
            t = m.sms[i]['time']
            s = ''.join(re.compile(r'[0-9]').findall(t))
        else:
            s = i
        filename = '%s/%s_%s_%s.txt' % (
          DIR,
          m.card['imei'],
          m.sms[i]['msisdn'],
          s)
        f = open(filename,'w')
        f.write(content)
        f.close()
        print 'Save to', filename
    if KEEP:
        return
    for i in m.sms.keys():
        m.delsms(i)

def console():
    daemon = Idle()
    daemon.start()
    print 'Type exit to finish.'
    while True:
        sleep(1)
        command = raw_input()
        if command.upper() == 'EXIT':
            break
        m.send( command )
    daemon.stop()


 
"""
SMS file format:

--------------
Field1: Value1
Field2: Value2

Message
--------------

putsms() example:

----------------
msisdn: +628170944042

Satu
Dua
Tiga
----------------

getsms() example:

----------------
msisdn: +628170944042
imei: 51101123456789 

Satu
Dua
Tiga
----------------
"""        
        
def parse_sms_file(filename):
    f = open(filename,'r')
    r = {}
    msg = None
    for line in f.readlines():
        if msg is None:
            try:
                field, value = line.split(': ')
                r.update({field: value.strip()})
            except ValueError:
                msg = ''
        else:
            msg += line
    r['message'] = msg
    return r
 
def usage():
    sys.stderr.write("""USAGE: %s [options]
    modem - A Modem Handler

    options:
    -d, --device=DEVICE         : device, default %s 
    -b, --baud=BAUD             : baudrate, default %s 
    
    -o, --operator              : show SIM operator
    
    -u, --ussd=COMMAND          : send USSD, ex: "*555#"
    
    -p, --putsms=FILE           : send SMS
    
    -g, --getsms=DIR            : get SMS
    -k, --keep                  : do not delete SMS after got it
    
    -n, --phonebook             : show phonebook
    
    -c, --call=MSISDN           : voice call
    -w, --waitforcommand        : wait for command, including:
                                  receiving call, make call, send SMS 

    -a, --simapp=items          : SIM Application
                                  ex: ""
                                      "DOMPETPULSA,08176785979,JEMPOL,JEMPOL,5k,OK,1234"
                                      "DOMPETPULSA,08179140068,BEBAS,SAPA,5k,OK,1234"
                                      "m-BCA,m-Info,Info Saldo,1234"
                                      "M-Tronik,Isi Pulsa,08159126773,RP 10.000,Reguler,123456,OK"

    -l, --console               : console for development

    -?, --help                  : this help


""" % (sys.argv[0], DEVICE, BAUD))


def show(data):
    if type(data) == type({}):
        for key in data.keys():
            print '%s: %s' % (key, data[key])
    else:
        print data


def search_file(filename):
    if os.path.exists(filename):
        return filename
    return os.path.join(os.path.split(__file__)[0], filename)



def create_timed_rotating_log(path):
    """"""
    logger = logging.getLogger("Rotating Log")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.formatter = formatter

    
 
    handler = TimedRotatingFileHandler(path,
                                       when="h",
                                       interval=1,
                                       backupCount=72)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(console_handler)
    return logger

def connect():
    while (True):
        DEVICE = findDev()
        dev = Modem(DEVICE, BAUD, logger)
        if not dev.serial:
            logger.error("fail to connect[%s] in baud[%s], waiting 30s to retry..." % (DEVICE, BAUD))
            time.sleep(30)
        else:
            break;
    dev.reset()
    dev.initsms()
    return dev;

def send_pdu(pdu, count=0):
    if count > 3:
        logger.error("pdu[%s] report retry times > 3, continue..." % pdu)
        return
    req = urllib2.Request(REPORT_CENTER, "pdu=" + pdu)  
    response = None
    try:  
        logger.info("pdu[%s] is ready to send to %s" % (pdu, REPORT_CENTER))
        response = urllib2.urlopen(req)
    except Exception as e:
        logger.error("report fail, reason:%s" % str(e))
        count = count + 1
        send_pdu(pdu, count)

def findDev():
    while(True): 
        logger.info("finding device...")
        for i in os.listdir("/dev"):
            if i.find("ttyUSB") != -1:
                logger.info("devide[%s] found..." % ("/dev/" + i))
                return "/dev/" + i 
        time.sleep(5)

TIMEOUT = conf.timeout
NUMBERS = ['0','1','2','3','4','5','6','7','8','9']
MSISDN_NUMBERS = ['+'] + NUMBERS
DTMF_KEYS = NUMBERS + ['*','#','A','B','C','D']
SMS_PDU, SMS_TEXT = 0, 1
OPERATOR_LIST_FILE = search_file('operators.txt')
SMS_ALL_PATTERNS = [
    re.compile(r'^\+CMGL: (.*),"(.*)","(.*)",,"((.*),((.*)(\+|\-)(.*)))"'),
    re.compile(r'^\+CMGL:(.*),"(.*)","(.*)","(.*)",(.*),(.*),(.*)')]

if __name__ == '__main__':
    import getopt
    import sys

    logger = create_timed_rotating_log(conf.log_file)

    logger.info("start transfering sms...")

    m = connect()

    while (True):
        try:
            m.getsms()
            for i in m.sms.keys():
                content = m.sms[i]['message']
                send_pdu(content)
                logger.info("get a sms, content[%s]" % m.sms[i]['message'])
                m.delsms(i)
            logger.info("sleeping 10s...")
            time.sleep(10)
        except Exception as e:
            logger.error("get sms fail, reason:%s" % str(e))
            logger.error("start to reconnect serial...")
            m = connect()




