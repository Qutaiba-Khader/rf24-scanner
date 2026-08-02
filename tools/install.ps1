<#
Install rf24scan onto a board already running stock MicroPython, and verify it.

This is the route that does not involve a custom .uf2 at all: flash stock
MicroPython once, then copy main.py onto the board's filesystem. It sidesteps
the frozen-code problem entirely and, unlike a .uf2, leaves you a REPL to read
tracebacks from.

    powershell -File tools\install.ps1
    powershell -File tools\install.ps1 -Port COM19 -SkipRadioTest

Every serial operation runs inside a killable job with a timeout, because
mpremote HANGS forever (surviving timeout) when the port is held by another
program - typically a browser tab still connected over Web Serial.
#>
param(
  [string]$Port = "",
  [switch]$SkipRadioTest
)

$ErrorActionPreference = "Stop"
$py = "C:\Python313\python.exe"
$root = Split-Path -Parent $PSScriptRoot
$main = Join-Path $root "firmware\main.py"

function Find-Port {
  if ($Port) { return $Port }
  $p = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
       Where-Object { $_.DeviceID -match 'VID_2E8A' -and $_.Name -match '\((COM\d+)\)' }
  foreach ($d in $p) { if ($d.Name -match '\((COM\d+)\)') { return $Matches[1] } }
  throw "No Raspberry Pi COM port found. Pass -Port COMxx."
}

function Test-PortFree($p) {
  # Opening a wedged COM port does not always fail fast - it can BLOCK inside
  # the driver, surviving Ctrl-C and leaving a process that must be force
  # killed, which wedges the port harder for everyone after it. So probe inside
  # a job that can be abandoned.
  $job = Start-Job -ArgumentList $p -ScriptBlock {
    param($p)
    try {
      $sp = New-Object System.IO.Ports.SerialPort($p, 115200)
      $sp.Open(); $sp.Close(); "free"
    } catch { "busy" }
  }
  $done = Wait-Job $job -Timeout 6
  $res = if ($done) { Receive-Job $job } else { "hung" }
  Stop-Job $job -ErrorAction SilentlyContinue
  Remove-Job $job -Force -ErrorAction SilentlyContinue
  return ($res -eq "free")
}

function Invoke-Mpremote($argline, $timeoutSec = 40) {
  $job = Start-Job -ArgumentList $py, $argline -ScriptBlock {
    param($py, $a)
    & $py -m mpremote $a.Split(' ') 2>&1 | Out-String
  }
  $done = Wait-Job $job -Timeout $timeoutSec
  if (-not $done) {
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    throw "mpremote timed out after ${timeoutSec}s - the port is probably held by another program."
  }
  $out = Receive-Job $job
  Remove-Job $job -Force -ErrorAction SilentlyContinue
  return $out
}

# ------------------------------------------------------------------ preflight
$p = Find-Port
Write-Host "Board on $p" -ForegroundColor Cyan

if (-not (Test-PortFree $p)) {
  Write-Host ""
  Write-Host "  $p is held by another program." -ForegroundColor Yellow
  Write-Host "  Close any browser tab connected to the scanner, close Thonny,"
  Write-Host "  or unplug and replug the board. Then run this again."
  exit 1
}
if (-not (Test-Path $main)) { throw "firmware/main.py not found at $main" }

# ------------------------------------------------------------------- install
Write-Host "Copying main.py to the board..." -ForegroundColor Cyan
Invoke-Mpremote "connect $p fs cp `"$main`" :main.py" | Write-Host

Write-Host "Verifying it is there..." -ForegroundColor Cyan
Invoke-Mpremote "connect $p fs ls" | Write-Host

# ------------------------------------------------------------- radio check
if (-not $SkipRadioTest) {
  Write-Host "Testing the nRF24 over SPI (this is the answer we still do not have)..." -ForegroundColor Cyan
  $probe = @'
from machine import Pin, SPI
CE=Pin(2,Pin.OUT,value=0); CSN=Pin(5,Pin.OUT,value=1)
spi=SPI(0,baudrate=8000000,polarity=0,phase=0,bits=8,sck=Pin(6),mosi=Pin(7),miso=Pin(4))
w=bytearray(2); r=bytearray(2)
def wr(g,v):
    w[0]=0x20|g; w[1]=v; CSN(0); spi.write(w); CSN(1)
def rd(g):
    w[0]=g; w[1]=0xFF; CSN(0); spi.write_readinto(w,r); CSN(1); return r[1]
print("CONFIG=0x%02X EN_AA=0x%02X SETUP_AW=0x%02X RF_SETUP=0x%02X STATUS=0x%02X" % (rd(0),rd(1),rd(3),rd(6),rd(7)))
ok=True
for probe in (0x2A,0x15,0x01,0x4C):
    wr(5,probe); got=rd(5)
    print("  wrote 0x%02X read 0x%02X %s" % (probe,got,"OK" if got==probe else "MISMATCH"))
    if got!=probe: ok=False
print("RADIO_PRESENT:", ok)
'@
  $tmp = Join-Path $env:TEMP "rf24_probe.py"
  $probe | Set-Content -Path $tmp -Encoding UTF8
  Invoke-Mpremote "connect $p run `"$tmp`"" | Write-Host
}

Write-Host ""
Write-Host "Resetting the board so main.py runs..." -ForegroundColor Cyan
Invoke-Mpremote "connect $p reset" 15 | Write-Host
Write-Host ""
Write-Host "Done. The scanner is now on the filesystem - no custom .uf2 needed." -ForegroundColor Green
Write-Host "Read it with:  python tools\scan.py --seconds 20 --html report.html"
