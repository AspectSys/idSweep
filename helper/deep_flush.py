import pyvisa
import time

rm = pyvisa.ResourceManager()

try:
    print("1. Opening Connection to GPIB0::17...")
    keithley = rm.open_resource("GPIB0::17::INSTR")
    
    # Temporarily drop timeout to 1 second so we can quickly drain the buffer
    keithley.timeout = 1000 
    
    print("2. Draining stuck responses from previous commands...")
    stuck_data_found = False
    while True:
        try:
            # Suck raw bytes out of the buffer until it throws a timeout
            junk = keithley.read_raw()
            print(f"   [Discarded stuck data]: {junk}")
            stuck_data_found = True
        except pyvisa.errors.VisaIOError:
            # A timeout here is GOOD! It means the buffer is finally empty.
            break
            
    if not stuck_data_found:
        print("   (Buffer was already empty)")

    print("3. Sending Hardware SDC (Selected Device Clear)...")
    keithley.clear()
    time.sleep(1) # Give the old Windows PC inside the Keithley a second to breathe
    
    print("4. Sending KXCI specific 'Abort' and 'Buffer Clear' commands...")
    keithley.write_termination = '\r\n'
    keithley.read_termination = None
    
    try:
        keithley.write("AB") # KXCI command to abort any running test
        time.sleep(0.5)
        keithley.write("BC") # KXCI command to clear internal buffers
        time.sleep(0.5)
    except Exception as e:
        print(f"   Write failed: {e}")

    print("5. Attempting KXCI 'ID' query again...")
    keithley.timeout = 5000 # Put timeout back to normal (5 seconds)
    
    # Use read_raw() to avoid termination character mismatches
    keithley.write("ID")
    response = keithley.read_raw() 
    print(f"\n[SUCCESS] Instrument replied: {response.decode('ascii', errors='ignore').strip()}")

except Exception as e:
    print(f"\n[FATAL ERROR] {e}")