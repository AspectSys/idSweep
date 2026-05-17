import pyvisa
import time

rm = pyvisa.ResourceManager()

print("Connecting to 4200-SCS on GPIB0::17...")
try:
    keithley = rm.open_resource("GPIB0::17::INSTR")
    
    # 1. THE FIX: Send a hardware Device Clear (DCL) to flush hung buffers
    print("Sending Device Clear (DCL) to flush buffers...")
    keithley.clear()
    time.sleep(0.5) # Give it half a second to process
    
    # 2. Configure the exact line-endings KXCI expects
    keithley.write_termination = '\r\n'
    keithley.read_termination = None # Rely on the GPIB cable's EOI line to stop reading
    keithley.timeout = 5000
    
    # 3. Ping the instrument using a standard VISA query
    print("Sending '*IDN?'...")
    response = keithley.query("*IDN?")
    print(f"\n[SUCCESS] Instrument replied: {response.strip()}")

except Exception as e:
    print(f"\n[FAILED] Error: {e}")
    
    # If standard SCPI *IDN? fails, let's try the legacy KXCI identify command
    print("\nAttempting fallback KXCI 'ID' command...")
    try:
        keithley.clear()
        time.sleep(0.5)
        response2 = keithley.query("ID")
        print(f"[SUCCESS] Fallback replied: {response2.strip()}")
    except Exception as e2:
        print(f"[FAILED] Fallback Error: {e2}")
