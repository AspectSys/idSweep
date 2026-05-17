import pyvisa
import time
import pandas as pd
from keithley4200 import Device as Keithley4200SCS

# =================================================================
# 1. CONFIGURATION & EXCEL SETUP
# =================================================================
# Update this path if your Excel file is located somewhere else
excel_path = r"C:\Users\Public\Documents\SweepMe!\Settings\ParameterMatrix_DF.xlsx"

smu_port_address = "GPIB0::17::INSTR"
switch_port_address = "GPIB0::18::INSTR"

print(f"Loading Matrix Configs from: {excel_path}")
df = pd.read_excel(excel_path, sheet_name="MatrixConfigs")

# Prepare a list to save our final data
test_results = []

# =================================================================
# 2. HELPER CLASSES & FUNCTIONS
# =================================================================
class PysweepmePortWrapper:
    """Wraps PyVISA resource to emulate SweepMe!'s internal port structure."""
    def __init__(self, pyvisa_resource):
        self.port = pyvisa_resource 
        self.port.read_termination = '\r\n'
        self.port.write_termination = '\r\n'
        self.port.timeout = 10000 # 10 seconds timeout
        
    def write(self, cmd): self.port.write(cmd)
    def read(self): return self.port.read()
    def query(self, cmd): return self.port.query(cmd)

def apply_matrix_config(matrix_visa, config_string):
    """Translates strings like 'A10;C5;B1' into Keithley 707A GPIB commands."""
    # 1. Open all crosspoints to start fresh ('E0' = Open All, 'X' = Execute)
    matrix_visa.write("E0X") 
    
    # Skip if cell is empty
    if pd.isna(config_string) or str(config_string).strip() == "":
        return
        
    # 2. Split string and close specified relays
    crosspoints = str(config_string).split(";")
    for cp in crosspoints:
        cp = cp.strip()
        if cp:
            # 'C' + <crosspoint> + 'X' (e.g., "CA10X")
            matrix_visa.write(f"C{cp}X")
    
    # 3. Wait for mechanical relays to settle
    time.sleep(0.2)

# =================================================================
# 3. CONNECT TO INSTRUMENTS
# =================================================================
rm = pyvisa.ResourceManager()

print(f"Connecting to Switch Matrix on {switch_port_address}...")
matrix = rm.open_resource(switch_port_address)
matrix.write("RX") # Reset the matrix (open all relays)

print(f"Connecting to 4200-SCS on {smu_port_address}...")
keithley_visa = rm.open_resource(smu_port_address)
shared_smu_port = PysweepmePortWrapper(keithley_visa)

# =================================================================
# 4. INITIALIZE SMU CHANNELS 
# =================================================================
def setup_smu(channel_name):
    smu = Keithley4200SCS()
    smu.port = shared_smu_port 
    smu.apply_gui_parameters({
        "Port": smu_port_address, 
        "Channel": channel_name,
        "SweepMode": "Voltage in V", 
        "Range": "Auto",
        "Speed": "Medium", 
        "Compliance": 100e-6, # 100 uA current limit
        "Average": "1"
    })
    smu.connect()
    smu.initialize()
    smu.configure()
    return smu

print("Configuring SMU1 (Cathode) and SMU2 (Anode)...")
smu_cathode = setup_smu("SMU1")
smu_anode = setup_smu("SMU2")

# =================================================================
# 5. RUN THE AUTOMATED TEST LOOP
# =================================================================
# Define the custom sweep profile exactly as shown in your GUI screenshot
dark_current_sweep_profile = [
    {"voltage": -1.25, "hold_time": 1.0},
    {"voltage":  2.50, "hold_time": 3.0}
]

try:
    # Iterate through every row (pin) in the Excel file
    for index, row in df.iterrows():
        pin_name = row['MeasuredPin']
        dark_curr_config = row['DarkCurrent']
        
        print(f"\n{'='*70}")
        print(f"Testing Pin: {pin_name} | Matrix Routing: {dark_curr_config}")
        print(f"{'='*70}")
        
        # 1. Apply the matrix routing for this specific pin
        apply_matrix_config(matrix, dark_curr_config)
        
        # 2. Keep Anode fixed at 0V
        smu_anode.value = 0.0
        smu_anode.apply()
        
        print(f"{'Target (V)':>10} | {'Hold (s)':>8} | {'Cathode Meas (V)':>16} | {'Cathode Dark Current (A)':>24}")
        print("-" * 70)

        # 3. Run the Sweep Profile for this pin
        for step in dark_current_sweep_profile:
            target_v = step["voltage"]
            hold_s = step["hold_time"]
            
            # Apply Target Voltage
            smu_cathode.value = target_v
            smu_cathode.apply()
            
            # Wait for Hold Time (Settling / Soak)
            time.sleep(hold_s)
            
            # Trigger Measurement
            smu_cathode.measure()
            
            # Read Results [0] = Voltage, [1] = Current
            results = smu_cathode.call() 
            measured_v = results[0]
            measured_i = results[1]
            
            print(f"{target_v:10.2f} | {hold_s:8.1f} | {measured_v:16.4f} | {measured_i:24.3e}")
            
            # Save data to our results list
            test_results.append({
                "Pin": pin_name,
                "Routing": dark_curr_config,
                "Target_V": target_v,
                "Measured_V": measured_v,
                "DarkCurrent_A": measured_i
            })

finally:
    # =================================================================
    # 6. SAFE SHUTDOWN & DATA EXPORT
    # =================================================================
    print("\nTesting complete. Resetting matrix and powering off SMUs...")
    matrix.write("E0X") # Open all relays
    
    smu_cathode.poweroff()
    smu_anode.poweroff()
    
    smu_cathode.unconfigure()
    smu_anode.unconfigure()
    
    smu_cathode.deinitialize()
    smu_anode.deinitialize()
    
    # Save the gathered data to a CSV file
    if test_results:
        results_df = pd.DataFrame(test_results)
        results_df.to_csv("DarkCurrent_Results.csv", index=False)
        print("Data successfully saved to 'DarkCurrent_Results.csv'!")