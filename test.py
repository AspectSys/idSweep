import pyvisa
import time
from keithley4200 import Device as Keithley4200
from keithley707a import Device as Keithley707A

# =================================================================
# 1. HELPER: PORT WRAPPER (Handles EOL characters)
# =================================================================
class PortWrapper:
    """Wraps PyVISA resource to emulate SweepMe!'s internal port structure."""
    def __init__(self, resource, write_eol, read_eol=None):
        self.port = resource
        self.port.write_termination = write_eol
        self.port.read_termination = read_eol  # None = rely on GPIB EOI line
        self.port.timeout = 5000 # 5 seconds
        
    def write(self, cmd): self.port.write(cmd)
    def read(self): return self.port.read()
    def query(self, cmd): return self.port.query(cmd)

# =================================================================
# 2. FIND DEVICES
# =================================================================
print("Initializing VISA Resource Manager...")
rm = pyvisa.ResourceManager()

# List all cables/instruments the PC can currently see
resources = rm.list_resources()
print(f"Found {len(resources)} VISA resources attached to this PC:")
for res in resources:
    print(f"  - {res}")

# =================================================================
# 3. TEST KEITHLEY 4200-SCS
# =================================================================
print("\n" + "="*50)
print("TESTING: Keithley 4200-SCS (GPIB0::17::INSTR)")
print("="*50)

try:
    res_4200 = rm.open_resource("GPIB0::17::INSTR")
    smu_driver = Keithley4200()
    
    # DCL flushes any hung buffers — essential for KXCI mode
    print("Sending Device Clear (DCL)...")
    res_4200.clear()
    time.sleep(0.5)

    # write_termination='\r\n' as KXCI expects; read_termination=None uses GPIB EOI
    smu_driver.port = PortWrapper(res_4200, write_eol='\r\n')
    smu_driver.command_set = "US" # Set to KXCI Mode
    
    # 4200-SCS in KXCI mode uses 'ID' not '*IDN?'
    print("Sending 'ID' command (KXCI identifier)...")
    idn_response = res_4200.query("ID")
    print(f"[SUCCESS] Device responded: {idn_response.strip()}")

except Exception as e:
    print(f"[FAILED] Could not talk to 4200-SCS. Error: {e}")


# =================================================================
# 4. TEST KEITHLEY 707A MATRIX
# =================================================================
print("\n" + "="*50)
print("TESTING: Keithley 707A Matrix (GPIB0::18::INSTR)")
print("="*50)

try:
    res_707a = rm.open_resource("GPIB0::18::INSTR")
    matrix_driver = Keithley707A()
    
    # The 707A driver code you provided specifically requests \r (Carriage Return only)
    matrix_driver.port = PortWrapper(res_707a, write_eol='\r')
    
    print("Initializing matrix (sending 'T4X')...")
    matrix_driver.initialize()
    
    print("Opening all relays safely (sending 'P0X')...")
    matrix_driver.open_all_crosspoints()
    
    print("[SUCCESS] 707A connected and all crosspoints reset!")

except Exception as e:
    print(f"[FAILED] Could not talk to 707A Matrix. Error: {e}")

print("\nHardware test finished.")