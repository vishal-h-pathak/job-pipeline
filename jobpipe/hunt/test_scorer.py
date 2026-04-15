from dotenv import load_dotenv
load_dotenv()
from scorer import score_job

result = score_job(
    title="Computational Neuroscientist",
    company="eon.systems",
    location="Remote",
    description=(
        "At Eon we are building embodied digital twins of animals starting "
        "with Drosophila and mouse. This role involves using FlyGym and "
        "NeuroMechFly to simulate Drosophila behaviors in MuJoCo, integrating "
        "connectome-based brain simulations using Brian2 and the Gymnasium "
        "API, and creating compelling visualizations of virtual fly behavior. "
        "Experience with computational neuroscience, spiking neural networks, "
        "and embodied simulation required."
    ),
)
print(result)
