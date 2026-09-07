from hako2epub.app import HakoApp

def main():
    app = HakoApp(
        formal_name="hako2epub",
        app_id="com.quantrancse.hako2epub",
        version="2.4.0"
    )
    app.main_loop()
