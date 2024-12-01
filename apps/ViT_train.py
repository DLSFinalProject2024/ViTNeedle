# import numpy as np

import sys

sys.path.append("python/")
import needle as ndl

import needle.nn as nn
sys.path.append("./")
from apps.models import ResNet9
from apps.simple_ml import train_cifar10, evaluate_cifar10
from needle.data import DataLoader, CIFAR10Dataset
import math


if __name__ == "__main__":
    # Load CIFAR-10 dataset
    train_dataset = CIFAR10Dataset(base_folder="./data/cifar-10-batches-py", train=True)
    test_dataset = CIFAR10Dataset(base_folder="./data/cifar-10-batches-py", train=False)
    print(f"len(train_dataset) = {len(train_dataset)}")
    print(f"len(test_dataset) = {len(test_dataset)}")

    # Create DataLoader instances
    batch_size = 64
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    #device = ndl.cuda() if ndl.cuda().enabled() else ndl.cpu()
    device = ndl.cpu()
    # Define Vision Transformer model
    model = nn.VisionTransformer(
        img_size=(32, 32),
        patch_size=4,
        in_channels=16,
        num_classes=10,
        embed_dim=64,
        num_blocks=1,
        num_heads=8,
        dim_head=8,
        mlp_hidden_dim=128,
        dropout=0.1,
        device=device,
    )

    #print(f"len(self.dattn.parmeters) = {sum([math.prod(each_w.shape) for each_w in model.dattn.fn.modules[1].parameters()])}")
    print(f"len(self.dattn.parmeters) = {sum([math.prod(each_w.shape) for each_w in model.dattn.fn.modules[0].parameters()])}")
    print(f"len(self.attn.parmeters) = {sum(math.prod(each_w.shape) for each_w in model.transformer_blocks.modules[0].layer1.modules[0].parameters())}")
    print(f"len(self.dattn_model.parmeters) = {sum(math.prod(each_w.shape) for each_w in model.parameters())}")

    # Train the model
    train_cifar10(model, train_loader, n_epochs=10, optimizer=ndl.optim.Adam, lr=0.001, weight_decay=0.001)

    # Evaluate the model
    evaluate_cifar10(model, test_loader)