#  ▄▄   ▄▄ ▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄▄ ▄▄    ▄ ▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄   ▄▄   ▄▄▄▄▄▄▄ ▄▄▄▄▄▄▄ ▄▄▄ 
# █  █ █  █      █       █   █  █  █ █       █       █  █ █  █  █       █       █   █
# █  █▄█  █  ▄   █       █   █   █▄█ █    ▄▄▄█▄     ▄█  █▄█  █  █   ▄   █    ▄  █   █
# █       █ █▄█  █     ▄▄█   █       █   █▄▄▄  █   █ █       █  █  █▄█  █   █▄█ █   █
# █▄     ▄█      █    █  █   █  ▄    █    ▄▄▄█ █   █ █       █  █       █    ▄▄▄█   █
#   █   █ █  ▄   █    █▄▄█   █ █ █   █   █▄▄▄  █   █  █     █   █   ▄   █   █   █   █
#   █▄▄▄█ █▄█ █▄▄█▄▄▄▄▄▄▄█▄▄▄█▄█  █▄▄█▄▄▄▄▄▄▄█ █▄▄▄█   █▄▄▄█    █▄▄█ █▄▄█▄▄▄█   █▄▄▄█

# by aimadnet
# contact: t.me/aimadnet

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import routes
import proxy

app = FastAPI(
    title="YacineTV",
    description="This is an unofficial api wrapper for yacineapp.tv in python",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
app.include_router(proxy.router)



@app.get("/")
async def index():
    return {"message": "Hello World"}
