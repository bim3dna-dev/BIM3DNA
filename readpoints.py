file_path = "C:\\Zonneveld\\Point_Clouds\\Huizingalaan_2.pts"

try:
    with open(file_path, "r") as file:
        print("First 10 lines of the .pts file:")
        for i in range(10):
            line = file.readline()
            if not line:
                break
            print(line.strip())
except Exception as e:
    print("Error reading the .pts file:", str(e))
