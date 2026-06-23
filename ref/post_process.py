# author: Franz Hempel
# created at: 2025-01-01
# company/institute: SweepMe!
import datetime
import os.path
import re

from pysweepme import FolderManager as FoMa
FoMa.addFolderToPATH()

import ParameterManager
ParM = ParameterManager.ParameterManager()


class Main:
    execution = "process"
    variables = []
    units = []

    arguments = {
        "Product Name": "Some Product",
        "Operator": "aS staff",
        "Wafer ID": "01",
        "Lot ID": "T2825",
        "Tester Device Pos X": "1",
        "Tester Device Pos Y": "2",
        "Temperature": "-18,0",
        "Device No": "00001",
        # " ": None,  # Empty key to separate the header from the results
        "Pin": "SomePin",
        "Dark Current -1.25V [A]": "0.0",
        "Dark Current 2.5V [A]": "0.0",
        "Guard Leakage 0V [A]": "0.0",
        "Guard Leakage 2.5V [A]": "0.0",
        "Series Resistance [Ohm]": "0.0",
        "tance [F]": "0.0",
        "mdf file path": "my_file.mdf",
    }

    def __init__(self) -> None:
        """Define parameters."""
        self.device_number = "None"
        """Save the prober positions. If it changes, create a new file."""

        self.pin_number: int = 1
        self.time_needed_for_previous_measurements: float = 0
        self.soft_bin = 1

    def main(self, **kwargs) -> None:
        """
        <h2>Diode Measurement Analysis</h2>
        This script is used to analyze the results of a diode measurement and write them to a text file.
        The file is save in the temp folder and can be saved via the standard SweepMe! save procedure.

        Args:
        <ul>
            <li>Product Name [str]: Name of the product.</li>
            <li>Operator [str]: Name of the operator.</li>
            <li>Wafer ID [str]: ID of the wafer.</li>
            <li>lot Number [str]: Number of the lot.</li>
            <li>Tester Device Pos X [str]: X position of the tester device.</li>
            <li>Tester Device Pos Y [str]: Y position of the tester device.</li>
            <li>Temperature [str]: Temperature of the measurement.</li>
            <li>Device No [str]: Number of the device.</li>
            <li>Pin [str]: Pin name like A, B, C, D.</li>
            <li>Dark Current [A]: Dark current of the diode [pA].</li>
            <li>Guard Leakage [A]: Guard leakage of the diode [nA].</li>
            <li>Series Resistance [Ohm]: Series resistance of the diode [Ohm].</li>
            <li>Capacitance [F]: Capacitance of the diode [pF].</li>
        </ul>
        """
        # Read the arguments
        product_name = kwargs["Product Name"]
        operator = kwargs["Operator"]
        wafer_id = kwargs["Wafer ID"]
        lot_id = kwargs["Lot ID"]
        tester_device_pos_x = kwargs["Tester Device Pos X"]
        tester_device_pos_y = kwargs["Tester Device Pos Y"]
        temperature = kwargs["Temperature"]
        device_no = kwargs["Device No"]

        pin = kwargs["Pin"]
        dark_current_reverse = float(kwargs["Dark Current -1.25V [A]"])
        dark_current = float(kwargs["Dark Current 2.5V [A]"])
        guard_leakage_0v = float(kwargs["Guard Leakage 0V [A]"])
        guard_leakage_2v = float(kwargs["Guard Leakage 2.5V [A]"])
        series_ = float(kwargs["Series Resistance [Ohm]"])
        capacitance = float(kwargs["Capacitance [F]"])

        mdf_file_path = kwargs["mdf file path"]

        # Footer handling: We add the footer every time a new pin is measured.
        # To ensure we only have one footer per file, we remove the existing footer from the previous run before adding
        # the new results.

        result_file_path = FoMa.get_path("TEMP") + f"/result_{int(device_no):03d}.txt"

        # if the file already exists, load the content
        if os.path.exists(result_file_path):
            with open(result_file_path, "r") as file:
                lines = file.readlines()
        else:
            lines = []

        # If the file has content, remove the footer (last n lines)
        footer_length = 3  # TODO Joerg: Update this to the actual length of the footer
        if len(lines) > footer_length:
            lines = lines[:-footer_length]

        # Create the new content from the results
        # If the device number changes, a header is added
        if device_no != self.device_number:
            self.device_number = device_no

            # reset the test entries
            self.pin_number = 1  # reset the pin-number for the new device
            self.soft_bin = 1

            # get device number form mdf file
            device_number = self.get_die_index_from_mdf(mdf_file_path, int(tester_device_pos_x), int(tester_device_pos_y))

            header = self.generate_header(
                product_name=product_name,
                operator=operator,
                wafer_id=wafer_id,
                lot_id=lot_id,
                tester_device_pos_x=tester_device_pos_x,
                tester_device_pos_y=tester_device_pos_y,
                temperature=temperature,
                device_no=device_number,
            )
            lines.extend(header.splitlines(keepends=True))

            # Guard Leakage is only measured once per device, so we add it here
            lines.extend(self.generate_guard_leakage_0V_entry(guard_leakage_0v))
            lines.extend(self.generate_guard_leakage_2V_entry(guard_leakage_2v))
        else:
            self.pin_number += 1

        # Add the new results
        lines.extend(self.generate_dark_current_reverse_entry(dark_current_reverse, pin))
        lines.extend(self.generate_dark_current_entry(dark_current, pin))
        lines.extend(self.generate_series_resistance_entry(series_resistance, pin))
        lines.extend(self.generate_capacitance_entry(capacitance, pin))

        # add the footer
        lines.extend(self.generate_footer().splitlines(keepends=True))

        # Write the content to the file
        with open(result_file_path, "w") as file:
            file.writelines(lines)
        
        # last pin
        if pin == "A":
            self.time_needed_for_previous_measurements = ParM.get_parameter("Time_elapsed_s")

    @staticmethod
    def generate_header(product_name: str, operator: str, wafer_id: str, lot_id: str,
                        tester_device_pos_x: str, tester_device_pos_y: str,
                        temperature: str, device_no: int
                        ) -> str:
        """Generate a header for the result file."""
        # Measurement time_stamp
        time_stamp = ParM.get_parameter("Time_stamp")
        time_stamp_str = time_stamp.strftime("%d.%m.%y %H:%M:%S")

        header = (
            f"Product Name\t{product_name}\n"
            f"Time\t{time_stamp_str}\n"
            f"Operator Name\t{operator}\n"
            "Test Station\taS 2\n"
            f"LOT ID\t{lot_id}\n"
            f"Wafer ID\t{wafer_id}\n"
            f"Tester Device Pos X\t{tester_device_pos_x}\n"
            f"Tester Device Pos Y\t{tester_device_pos_y}\n"
            f"Temperature\t{str(temperature).replace('.', ',')}\n"
            f"Device No\t{device_no}\n"
        )

        return header

    def generate_dark_current_reverse_entry(self, dark_current: float, pin: str) -> list[str]:
        """Generate a dark current entry for the result file."""
        return self.generate_test_entry(
            number=3,
            name=f"Pin {pin} Dark Current -1.25V [mA]",
            value=dark_current * 1e3,  # in mA
            low_limit=0.0,
            high_limit=0.1,
            bin=8,
        )

    def generate_dark_current_entry(self, dark_current: float, pin: str) -> list[str]:
        """Generate a dark current entry for the result file."""
        return self.generate_test_entry(
            number=4,
            name=f"Pin {pin} Dark Current 2.5V [pA]",
            value=dark_current * 1e12,  # in pA
            low_limit=0.0,
            high_limit=0.1,
            bin=8,
        )

    def generate_guard_leakage_0V_entry(self, guard_leakage: float) -> list[str]:
        """Generate a guard leakage entry for the result file."""
        return self.generate_test_entry(
            number=1,
            name="Guard Leakage 0V [nA]",
            value=guard_leakage * 1e9,  # in nA
            low_limit=0.0,
            high_limit=0.1,
            bin=7,
        )

    def generate_guard_leakage_2V_entry(self, guard_leakage: float) -> list[str]:
        """Generate a guard leakage entry for the result file."""
        return self.generate_test_entry(
            number=2,
            name="Guard Leakage 2.5V [nA]",
            value=guard_leakage * 1e9,  # in nA
            low_limit=0.0,
            high_limit=0.1,
            bin=7,
        )

    def generate_series_resistance_entry(self, series_resistance: float, pin: str) -> list[str]:
        """Generate a series resistance entry for the result file."""
        return self.generate_test_entry(
            number=5,
            name=f"Pin {pin} Series Resistance [Ohm]",
            value=series_resistance,
            low_limit=0.0,
            high_limit=0.1,
            bin=5,
        )

    def generate_capacitance_entry(self, capacitance: float, pin: str) -> list[str]:
        """Generate a capacitance entry for the result file."""
        return self.generate_test_entry(
            number=6,
            name=f"Pin {pin} Capacitance [pF]",
            value=capacitance * 1e12,  # in pF
            low_limit=0.0,
            high_limit=0.1,
            bin=6,
        )

    def generate_test_entry(self, number, name, value, low_limit: float, high_limit: float, bin: int = 1) -> list[str]:
        """Generate a test entry for the result file.

        The format is: test number, test name, bin, pass/fail, test value, low limit, high limit
        """
        pass_fail = 1 if low_limit <= value <= high_limit else 0
        binning = 1 if pass_fail == 1 else bin

        if binning > self.soft_bin:
            self.soft_bin = binning

        test_number = f"{self.pin_number:02d}{number:02d}"

        low_str = str(low_limit).replace(".", ",")
        value_str = str(value).replace(".", ",")
        high_str = str(high_limit).replace(".", ",")
        test_entry = f"{test_number}\t{name}\t{binning}\t{pass_fail}\t{low_str}\t{value_str}\t{high_str}\n"
        return test_entry.splitlines(keepends=True)

    def generate_footer(self) -> str:
        """Generate a footer for the result file."""
        measurement_time = ParM.get_parameter("Time_elapsed_s") - self.time_needed_for_previous_measurements
        formatted_time = str(datetime.timedelta(seconds=int(measurement_time)))
        device_passed = 1 if self.soft_bin == 1 else 0
        return (
            f"Total Time\t{formatted_time}\n"
            f"Soft Bin\t{self.soft_bin}\n"
            f"Pass\t{device_passed}\n"
        )

    @staticmethod
    def get_die_index_from_mdf(mdf_file_path: str, ref_x, ref_y) -> int:
        """Get the die index from the MDF file based on the x and y coordinates."""
        prob_pattern = re.compile(r'^PROB=(\d+),(\d+)')
        prob_list = []

        with open(mdf_file_path, 'r') as f:
            for line in f:
                match = prob_pattern.match(line.strip())
                if match:
                    x, y = int(match.group(1)), int(match.group(2))
                    prob_list.append((x, y))

        try:
            index = prob_list.index((ref_x, ref_y)) + 1  # 1-based index
            return index
        except:
            return -1
