from dynatrace import init

init()

from travel_agent import app

if __name__ == "__main__":
    app.run()
